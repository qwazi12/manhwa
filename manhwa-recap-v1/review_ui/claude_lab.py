"""TEST LAB — an independent chapter pipeline driven by Claude.

This is NOT a view onto the main board and it does not borrow anything the main
system produced. You give it a chapter URL and it builds a whole chapter of its
own, from the download onward:

    download pages                          [shared — there is no "Claude way"
        |                                    to fetch a web page]
    cut pages into panels                   [SWITCHABLE: Claude or YOLO]
        |
    read each panel: OCR + description      [Claude]
        |
    choose the crop framing per panel       [Claude]
        |
    write the narration script              [Claude]
        |
    place each line on a panel, in order    [Claude]
        |
    voice the script                        [shared — the existing TTS path,
        |                                    deliberately unchanged]
    build render segments
        |
    a REAL project

That last line is the point. The lab does not end in a report — it ends in an
ordinary project directory with the same files every other chapter has, so the
existing board, Check tab, approve, export and Review pages all work on it with
no special cases. "See the result like the board" is not a feature that had to
be built; it is what falls out of producing a real project.

INDEPENDENCE: the lab writes only its own project directory, named
<chapter>-lab-claude or <chapter>-lab-yolo. It never reads or writes the main
system's chapter, so both can exist for the same URL and be compared.

THE SPLITTER TOGGLE is the experiment's other half. YOLO is a trained panel
detector; Claude reading a page and deciding where the panels are is a
different approach entirely. Running both against the same chapter is the only
way to find out which cuts a page better.

COST: every Claude call goes through usage.gate('claude', ...), the same gate
as everything else, so the lab is capped by the same daily spend limit and
shows in the same header. The describe stage is checkpointed per batch, so a
restart resumes instead of paying twice.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import usage
import validator
import claude_pipeline as CP

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.dirname(HERE)
ROOT = os.path.abspath(os.path.join(RECAP, ".."))
PROJECTS = os.path.join(HERE, "projects")
PY = sys.executable

STAGES = ["scrape", "split", "read", "script", "place", "voice", "segment"]
SPLITTERS = ("claude", "yolo")

# Pages are downscaled before Claude looks at them. A manhwa page can be
# 800x8000; sending that whole costs a fortune and tells Claude nothing extra
# about where the panel borders are.
PAGE_MAX_PX = int(os.environ.get("LAB_PAGE_MAX_PX", 1400))

# A page that yields more than this many panels is almost certainly a
# mis-parse, not a real page.
MAX_PANELS_PER_PAGE = int(os.environ.get("LAB_MAX_PANELS_PER_PAGE", 14))


class LabError(RuntimeError):
    """A condition the operator must see."""


def lab_id(url, splitter):
    """Own project id, carrying the URL's slug AND which splitter ran, so the
    two variants of a chapter can coexist and be told apart at a glance."""
    import ingest
    return f"{ingest._slug(url)}-lab-{splitter}"


def lab_dir(url, splitter):
    return os.path.join(PROJECTS, lab_id(url, splitter))


# ------------------------------------------------- Claude page splitting
SPLIT_SYSTEM = """\
You are looking at one full page of a manhwa (a Korean comic read top to \
bottom, usually as a tall vertical strip).

Find the PANELS on this page and return a box for each one, in reading order \
(top to bottom; for panels side by side, left to right).

A panel is one framed picture. Report:
- The art panels that tell the story.
- Do NOT return a box for the gutters (the blank space between panels), for the \
page as a whole, or for a run of pure white/black spacing.
- DO return a box for a panel that is mostly text (a narration card, a title \
card, a credits page) and set kind accordingly — later stages need to know it \
exists so they can skip it.

Boxes are fractions of the page: x0, y0, x1, y1, each between 0.0 and 1.0, with \
x0 < x1 and y0 < y1. Include the panel's frame and any speech bubble that \
belongs to it, even if the bubble overlaps the border. It is better to include \
a few pixels of gutter than to clip art or text.

Tall vertical strips often have panels that run the full width — that is normal, \
and those boxes should be x0 near 0.0 and x1 near 1.0.

Set kind to "art" for a story panel, "text" for a narration/title card, and \
"credits" for a scanlation credits or watermark block."""

SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "box": {"type": "array", "items": {"type": "number"}},
                    "kind": {"type": "string",
                             "enum": ["art", "text", "credits"]},
                },
                "required": ["box", "kind"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["panels"],
    "additionalProperties": False,
}


def _page_image_block(path, max_px=PAGE_MAX_PX):
    from PIL import Image
    import base64
    import io as _io
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        if max(im.size) > max_px:
            sc = max_px / float(max(im.size))
            im = im.resize((max(1, int(im.width * sc)),
                            max(1, int(im.height * sc))), Image.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
    return ({"type": "image",
             "source": {"type": "base64", "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(
                            buf.getvalue()).decode("ascii")}}, w, h)


def _sane_box(box):
    try:
        x0, y0, x1, y1 = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    # A sliver is a mis-parse, not a panel.
    if (x1 - x0) < 0.05 or (y1 - y0) < 0.01:
        return None
    return [x0, y0, x1, y1]


def split_with_claude(pages_dir, crops_dir, model=None, progress=None):
    """Claude decides where the panels are, replacing the YOLO detector."""
    from PIL import Image
    model = model or CP.MODEL
    client = validator._client()
    pages = sorted(f for f in os.listdir(pages_dir)
                   if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                   and not f.startswith("_"))
    if not pages:
        raise LabError("no downloaded pages to split")

    made, tally = [], CP._Tally()
    for pi, fname in enumerate(pages, start=1):
        if progress:
            progress("split", f"Claude cutting page {pi}/{len(pages)}")
        path = os.path.join(pages_dir, fname)
        try:
            block, W, H = _page_image_block(path)
        except OSError:
            continue
        resp, meter, cost = validator._call_claude(
            client, model=model, max_tokens=8000,
            system=[{"type": "text", "text": SPLIT_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": CP.EFFORT,
                           "format": {"type": "json_schema",
                                      "schema": SPLIT_SCHEMA}},
            messages=[{"role": "user", "content": [
                block, {"type": "text",
                        "text": "Return the panels on this page, in reading "
                                "order."}]}])
        tally.add(meter, cost)

        boxes = []
        for item in validator._parse_json_reply(resp).get("panels", []):
            b = _sane_box(item.get("box"))
            if b:
                boxes.append((b, item.get("kind", "art")))
        if len(boxes) > MAX_PANELS_PER_PAGE:
            boxes = boxes[:MAX_PANELS_PER_PAGE]
        # Reading order: top to bottom, then left to right.
        boxes.sort(key=lambda t: (round(t[0][1], 3), t[0][0]))

        stem = f"page{pi:03d}"
        with Image.open(path) as im:
            im = im.convert("RGB")
            for bi, (b, kind) in enumerate(boxes, start=1):
                x0, y0, x1, y1 = b
                px = (int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H))
                if px[2] - px[0] < 8 or px[3] - px[1] < 8:
                    continue
                out = f"{stem}_panel_{bi:03d}.png"
                im.crop(px).save(os.path.join(crops_dir, out))
                made.append({"file": out, "kind": kind, "page": stem,
                             "box": b})
    if not made:
        raise LabError("Claude found no panels on any page")
    return made, tally.stats(pages=len(pages), panels=len(made))


def split_with_yolo(pages_dir, crops_dir, job_id="lab", progress=None):
    """The existing trained detector, run exactly as ingest runs it."""
    if progress:
        progress("split", "YOLO cutting pages into panels")
    env = {**os.environ, "RECAP_JOB_ID": job_id}
    log = os.path.join(os.path.dirname(crops_dir), "split.log")
    with open(log, "w", encoding="utf-8") as f:
        p = subprocess.run(
            [PY, os.path.join(ROOT, "panel-split", "split_panels.py"),
             "--input", pages_dir, "--out", crops_dir, "--batch"],
            cwd=os.path.join(ROOT, "panel-split"), env=env,
            stdout=f, stderr=f)
    if p.returncode != 0:
        raise LabError("the YOLO splitter failed — see split.log")
    made = [{"file": f, "kind": "art", "page": f.split("_")[0], "box": None}
            for f in sorted(os.listdir(crops_dir)) if f.endswith(".png")]
    if not made:
        raise LabError("the YOLO splitter produced no panels")
    return made, {"calls": 0, "cost_usd": 0.0, "prompt_tokens": 0,
                  "output_tokens": 0, "panels": len(made)}


# ------------------------------------------------------------- the run
def manifest(pdir):
    try:
        with open(os.path.join(pdir, "lab.json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_manifest(pdir, man):
    tmp = os.path.join(pdir, "lab.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(man, f, indent=2)
    os.replace(tmp, os.path.join(pdir, "lab.json"))


def run_lab(url, splitter="claude", model=None, progress=None, job_id="lab",
            voice=True, fresh=False):
    """Build a whole chapter, independently, with Claude making the calls."""
    if splitter not in SPLITTERS:
        raise LabError(f"splitter must be one of {SPLITTERS}")
    sys.path.insert(0, RECAP)
    sys.path.insert(0, os.path.join(RECAP, "hyperframes"))
    import scraper
    import beat_segmenter
    from segments import build_segments
    import ingest

    model = model or CP.MODEL
    usage.set_job(job_id)
    started = time.time()

    pdir = lab_dir(url, splitter)
    pages = os.path.join(pdir, "pages")
    crops = os.path.join(pdir, "crops")
    audio = os.path.join(pdir, "audio")
    for d in (pdir, pages, crops, audio, os.path.join(pdir, "clips")):
        os.makedirs(d, exist_ok=True)

    man = manifest(pdir) or {}
    man.update({"url": url, "splitter": splitter, "model": model,
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "running", "error": None,
                "lab": True,
                "cost_usd": man.get("cost_usd", 0.0),
                "calls": man.get("calls", 0),
                "passes": man.get("passes", {})})
    _save_manifest(pdir, man)

    def _prog(stage, msg):
        if progress:
            progress(f"{stage}: {msg}")

    def _absorb(name, st):
        man["passes"][name] = st
        man["cost_usd"] = round(man["cost_usd"] + st.get("cost_usd", 0.0), 6)
        man["calls"] += st.get("calls", 0)
        _save_manifest(pdir, man)

    try:
        # ---- 1. scrape (shared) ---------------------------------------
        have = [f for f in os.listdir(pages) if not f.startswith("_")]
        if fresh or not have:
            _prog("scrape", "downloading chapter pages")
            imgs = scraper.download_chapter(url, pages)
            if not imgs:
                raise LabError("the scraper downloaded no images — the URL is "
                               "wrong or the site blocked it")
            _prog("scrape", f"downloaded {len(imgs)} pages")
        else:
            _prog("scrape", f"{len(have)} pages already downloaded — reusing")

        # ---- 2. split (SWITCHABLE) -------------------------------------
        if fresh or not os.listdir(crops):
            shutil.rmtree(crops, ignore_errors=True)
            os.makedirs(crops, exist_ok=True)
            if splitter == "claude":
                made, st = split_with_claude(pages, crops, model=model,
                                             progress=lambda s, m: _prog(s, m))
            else:
                made, st = split_with_yolo(pages, crops, job_id=job_id,
                                           progress=lambda s, m: _prog(s, m))
            _absorb("split", st)
            with open(os.path.join(pdir, "panels.json"), "w",
                      encoding="utf-8") as f:
                json.dump(made, f, indent=2)
            _prog("split", f"{len(made)} panels")

        # ---- 3. read: OCR + description + crop framing (Claude) --------
        panel_files = sorted(f for f in os.listdir(crops) if f.endswith(".png"))
        if not panel_files:
            raise LabError("no panel crops to read")
        from PIL import Image
        panels = []
        for i, f in enumerate(panel_files, start=1):
            try:
                with Image.open(os.path.join(crops, f)) as im:
                    w, h = im.size
            except OSError:
                continue
            panels.append({"panel_id": os.path.splitext(f)[0], "file": f,
                           "width": w, "height": h, "_n": len(panels) + 1})

        prior = CP.read(pdir, "descriptions.json", []) or []
        prior = [d for d in prior if d.get("visual_description")]
        done = {d["panel_id"] for d in prior}

        def _ckpt(partial):
            merged = prior + [d for d in partial if d["panel_id"] not in done]
            merged.sort(key=lambda r: r.get("n", 0))
            CP._write(pdir, "descriptions.json", merged)

        _prog("read", f"Claude reading {len(panels) - len(done)} panels")
        fresh_descs, st = CP.describe(
            pdir, panels, model=model,
            progress=lambda m: _prog("read", m),
            on_batch=_ckpt, skip=done)
        _ckpt(fresh_descs)
        _absorb("read", st)
        descs = CP.read(pdir, "descriptions.json", [])
        if not descs:
            raise LabError("Claude produced no panel readings")

        # The lab's own descriptions.json IS the project's descriptions.json —
        # there is no sidecar here, because this project is Claude's from the
        # start rather than a variant of someone else's.
        shutil.copyfile(os.path.join(CP.out_dir(pdir), "descriptions.json"),
                        os.path.join(pdir, "descriptions.json"))

        # ---- 4. script (Claude) ----------------------------------------
        _prog("script", "Claude writing the narration")
        units, st = CP.script(pdir, descs, model=model,
                              progress=lambda m: _prog("script", m))
        _absorb("script", st)
        if not units:
            raise LabError("Claude wrote no narration")

        # ---- 5. place & sequence (Claude) ------------------------------
        _prog("place", f"Claude placing {len(units)} lines on {len(descs)} panels")
        scenes, st = CP.place(pdir, descs, units, model=model,
                              progress=lambda m: _prog("place", m))
        _absorb("place", st)
        with open(os.path.join(pdir, "script.json"), "w", encoding="utf-8") as f:
            json.dump([{"scene_id": s["scene_id"], "text": s["text"],
                        "panel_ids": s.get("panel_ids", [])} for s in scenes],
                      f, indent=2)
        with open(os.path.join(pdir, "script.txt"), "w", encoding="utf-8") as f:
            f.write("\n\n".join(s["text"] for s in scenes))

        # ---- 6. voice (SHARED, unchanged) ------------------------------
        beats = beat_segmenter.segment_beats_scenes(
            [{"scene_id": s["scene_id"], "text": s["text"],
              "panel_ids": s.get("panel_ids", [])} for s in scenes])
        if not beats:
            raise LabError("the script produced no beats")
        t = 0.0
        if voice:
            import server as srv
            _prog("voice", f"voicing {len(beats)} beats")
            for i, b in enumerate(beats):
                out = os.path.join(audio, f"beat_{b['index']:03d}.mp3")
                if not os.path.exists(out):
                    srv._synth_rest(b["text"], out)
                d = CP._audio_len(out)
                b["start"], b["end"] = round(t, 3), round(t + d, 3)
                nxt = beats[i + 1] if i + 1 < len(beats) else None
                if nxt is not None and "scene_id" in b and "scene_id" in nxt:
                    t += d + (0.6 if nxt["scene_id"] != b["scene_id"] else 0.25)
                else:
                    t += d + 0.35
                if i % 10 == 0:
                    _prog("voice", f"beat {i + 1}/{len(beats)} · {t:.0f}s")
        else:
            for b in beats:
                d = max(1.2, len(b["text"].split()) / 2.6)
                b["start"], b["end"] = round(t, 3), round(t + d, 3)
                t += d + 0.3

        # ---- 7. segments ------------------------------------------------
        _prog("segment", "building render segments")
        segs = _build(pdir, crops, descs, scenes, beats, build_segments)
        with open(os.path.join(pdir, "segments.json"), "w",
                  encoding="utf-8") as f:
            json.dump(segs, f, indent=2)

        series, chapter = ingest.parse_series_chapter(url)
        meta = {
            "id": lab_id(url, splitter), "url": url,
            "crops": crops, "audio": audio,
            "descriptions": os.path.join(pdir, "descriptions.json"),
            "n_segments": len(segs),
            "duration": round(segs[-1]["end"], 1) if segs else 0,
            "series": ingest.to_title_case(ingest.clean_series_slug(series)),
            "chapter": ingest.to_title_case(chapter),
            "match_method": f"claude-lab ({splitter} split)",
            # Stamped so this can never be mistaken for a normal chapter.
            "lab": True, "splitter": splitter,
            "lab_note": ("Independent experiment: panels, descriptions, crops, "
                         "script and placement by Claude. Panel cutting by "
                         + ("Claude" if splitter == "claude" else "YOLO")
                         + ". Audio by the standard TTS path."),
        }
        with open(os.path.join(pdir, "project.json"), "w",
                  encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        man.update({"status": "ok", "project": meta["id"],
                    "panels": len(descs), "units": len(scenes),
                    "segments": len(segs), "duration": meta["duration"],
                    "elapsed_sec": round(time.time() - started, 1)})
        _save_manifest(pdir, man)
        return man

    except (LabError, CP.PipelineError, validator.ValidatorError,
            usage.UsageCapExceeded) as e:
        man.update({"status": "error", "error": str(e),
                    "elapsed_sec": round(time.time() - started, 1)})
        _save_manifest(pdir, man)
        return man


def _build(pdir, crops, descs, scenes, beats, build_segments):
    """Lay Claude's placement onto the narration timeline, folding a unit's
    panels across its own window — the same shape the production board uses."""
    by_pid = {d["panel_id"]: d for d in descs}
    by_scene = {}
    for b in beats:
        by_scene.setdefault(b.get("scene_id"), []).append(b)

    shots = []
    for sc in scenes:
        sb = sorted(by_scene.get(sc["scene_id"], []), key=lambda x: x["start"])
        pids = [p for p in (sc.get("panel_ids") or []) if p in by_pid]
        if not sb or not pids:
            continue
        w0, w1 = sb[0]["start"], sb[-1]["end"]
        step = max(0.001, w1 - w0) / len(pids)
        for i, pid in enumerate(pids):
            s0 = w0 + i * step
            s1 = w1 if i == len(pids) - 1 else s0 + step
            mid = (s0 + s1) / 2.0
            b = next((x for x in sb if x["start"] <= mid <= x["end"]), None) \
                or min(sb, key=lambda x: abs((x["start"] + x["end"]) / 2 - mid))
            d = by_pid[pid]
            shots.append({
                "index": b["index"], "start": round(s0, 3), "end": round(s1, 3),
                "beat_text": b["text"], "panel_id": pid,
                "panel_file": os.path.join(crops, d.get("file") or f"{pid}.png"),
                "width": d.get("width"), "height": d.get("height"),
                "crop_bbox_norm": d.get("crop_bbox_norm") or [0.0, 0.0, 1.0, 1.0],
                "focus_source": "claude",
                "focus_reason": "crop framing chosen by Claude",
                "focus_confidence": 1.0,
            })
    if not shots:
        raise LabError("Claude placed no line on any panel — nothing to render")
    shots.sort(key=lambda s: s["start"])
    # Exact tiling, as matcher.build_timeline does: without this the pauses the
    # TTS rhythm inserts between scenes render as black frames.
    for i in range(len(shots) - 1):
        shots[i]["end"] = shots[i + 1]["start"]
    for sh in shots:
        sh["dur"] = round(sh["end"] - sh["start"], 3)
    return build_segments(shots)
