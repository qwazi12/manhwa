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
import claude_plus as PLUS
import claude_place as PLACE

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.dirname(HERE)
ROOT = os.path.abspath(os.path.join(RECAP, ".."))
PROJECTS = os.path.join(HERE, "projects")
PY = sys.executable

STAGES = ["scrape", "split", "read", "map", "script", "critique", "beats",
          "voice", "place", "crop", "segment"]
SPLITTERS = ("claude", "yolo")

# Pages are downscaled before Claude looks at them. A manhwa page can be
# 800x8000; sending that whole costs a fortune and tells Claude nothing extra
# about where the panel borders are.
PAGE_MAX_PX = int(os.environ.get("LAB_PAGE_MAX_PX", 1400))

# A page that yields more than this many panels is almost certainly a
# mis-parse, not a real page.
MAX_PANELS_PER_PAGE = int(os.environ.get("LAB_MAX_PANELS_PER_PAGE", 14))

# COVERAGE VALIDATION — the single most important safeguard on Claude cutting.
# Production gates its splitter on how much of the page's INK ends up inside a
# panel box, because a splitter that silently drops art produces a chapter with
# holes nobody notices. Claude was previously trusted with no such check: if it
# missed half a page, nothing said so. Same target as production.
COVERAGE_TARGET = float(os.environ.get("LAB_COVERAGE_TARGET", 0.85))
# A band of missed ink must be at least this tall to be worth recovering.
GAP_MIN_H = int(os.environ.get("LAB_GAP_MIN_H", 120))

# TALL-PANEL HANDLING. A webtoon strip returned as one box is not wrong, but it
# is unusable: it becomes one enormous on-screen image carrying several beats.
# Production has a four-level ladder for this; the lab needs at least the first
# two rungs or it silently under-splits every vertical chapter.
TALL_RATIO = float(os.environ.get("LAB_TALL_RATIO", 1.8))
TALL_TARGET_AR = float(os.environ.get("LAB_TALL_TARGET_AR", 1.4))
MIN_PANEL_PX = int(os.environ.get("LAB_MIN_PANEL_PX", 80))


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

# Used only on the retry, after a measured coverage miss. It names the failure
# instead of repeating the original instruction and hoping for a better roll.
SPLIT_RETRY_SUFFIX = """

YOUR PREVIOUS ANSWER MISSED ART. Measured against the page's own ink, {pct}% of \
the drawn content fell OUTSIDE the boxes you returned{bands}.

Go again and account for the WHOLE page top to bottom. Every region containing \
drawn art or readable text must sit inside some box. Panels in a vertical strip \
usually run the full width — those boxes should start near x=0.0 and end near \
x=1.0. It is far better to return one box too many than to leave art out."""

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


def _prod_split():
    """Production's splitter helpers, imported READ-ONLY.

    The coverage maths, the background estimate and the blank test are already
    correct and already battle-tested; re-deriving them in the lab would just be
    a second implementation to keep in sync. Nothing here calls into the
    production pipeline — only these pure functions are used.
    """
    pth = os.path.join(ROOT, "panel-split")
    if pth not in sys.path:
        sys.path.insert(0, pth)
    import split_panels as SP
    return SP


def _measure_coverage(gray, bg, boxes_px):
    """What fraction of the page's INK sits inside some box, plus the bands of
    missed content tall enough to be worth recovering."""
    SP = _prod_split()
    import numpy as np
    H = gray.shape[0]
    content = SP._content_rows(gray, bg)
    total = int(content.sum())
    if not total:
        return 1.0, []
    cov = SP._row_coverage(H, boxes_px)
    missed = content & ~cov
    coverage = float((content & cov).sum() / total)

    bands, run_start = [], None
    for y in range(H):
        if missed[y] and run_start is None:
            run_start = y
        elif not missed[y] and run_start is not None:
            if y - run_start >= GAP_MIN_H:
                bands.append([run_start, y])
            run_start = None
    if run_start is not None and H - run_start >= GAP_MIN_H:
        bands.append([run_start, H])
    return coverage, bands


def _recover_bands(gray, bg, bands, W):
    """Geometric gutter split over the bands Claude missed.

    This is production's pass 2, reused verbatim in spirit: whatever the primary
    detector left uncovered gets cut on its own gutters rather than being lost.
    """
    SP = _prod_split()
    out = []
    for y0, y1 in bands:
        region = gray[y0:y1, :]
        try:
            pieces = SP._split_axis(region, axis=0, bg_color=bg)
        except Exception:
            pieces = []
        if not pieces:
            pieces = [(0, y1 - y0)]
        for a, b in pieces:
            if b - a >= MIN_PANEL_PX:
                out.append([0, y0 + a, W, y0 + b])
    return out


def _split_tall(gray, bg, box, W):
    """Cut a too-tall box down to usable pieces.

    Rung 1: split on the box's own internal gutters (production's T1 fix, the
    one that stopped 7:1 strips reaching the board whole). Rung 2: if it is
    gutterless and still extreme, cut it into even pieces near a readable
    aspect ratio rather than shipping one enormous image carrying five beats.
    """
    SP = _prod_split()
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h / max(w, 1) < TALL_RATIO:
        return [box]

    region = gray[y0:y1, x0:x1]
    try:
        internal = SP._split_axis(region, axis=0, bg_color=bg)
    except Exception:
        internal = []
    if len(internal) > 1:
        out = []
        for a, b in internal:
            if b - a >= MIN_PANEL_PX:
                out.extend(_split_tall(gray, bg, [x0, y0 + a, x1, y0 + b], W))
        if out:
            return out

    target_h = max(MIN_PANEL_PX, int(w * TALL_TARGET_AR))
    n = max(1, int(round(h / float(target_h))))
    if n <= 1:
        return [box]
    step = h / float(n)
    return [[x0, int(y0 + i * step), x1,
             int(y0 + (i + 1) * step) if i < n - 1 else y1] for i in range(n)]


def split_with_claude(pages_dir, crops_dir, model=None, progress=None):
    """Claude decides where the panels are — then the result is MEASURED.

    The first version trusted Claude's boxes outright. If it missed half a page
    nothing said so, and the chapter simply came out short. Now every page is
    scored against its own ink, a miss gets one stricter retry that names what
    was missed, and anything still uncovered is recovered geometrically. Tall
    boxes are cut down, blanks are dropped, and the per-page numbers are kept so
    the run can be audited instead of trusted.
    """
    from PIL import Image
    import numpy as np
    SP = _prod_split()
    model = model or CP.MODEL
    client = validator._client()
    pages = sorted(f for f in os.listdir(pages_dir)
                   if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                   and not f.startswith("_"))
    if not pages:
        raise LabError("no downloaded pages to split")

    made, tally = [], CP._Tally()
    page_stats, blanks = [], 0

    for pi, fname in enumerate(pages, start=1):
        if progress:
            progress("split", f"Claude cutting page {pi}/{len(pages)}")
        path = os.path.join(pages_dir, fname)
        try:
            block, W, H = _page_image_block(path)
        except OSError:
            continue
        with Image.open(path) as _im:
            gray = np.array(_im.convert("L"))
        bg = SP._estimate_background_color(gray)

        def _ask(extra=""):
            resp, meter, cost = validator._call_claude(
                client, model=model, max_tokens=8000,
                system=[{"type": "text", "text": SPLIT_SYSTEM + extra,
                         "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": CP.EFFORT,
                               "format": {"type": "json_schema",
                                          "schema": SPLIT_SCHEMA}},
                messages=[{"role": "user", "content": [
                    block, {"type": "text",
                            "text": "Return the panels on this page, in "
                                    "reading order."}]}])
            tally.add(meter, cost)
            got = []
            for item in validator._parse_json_reply(resp).get("panels", []):
                b = _sane_box(item.get("box"))
                if b:
                    got.append((b, item.get("kind", "art")))
            return got[:MAX_PANELS_PER_PAGE]

        boxes = _ask()
        px = [[int(b[0] * W), int(b[1] * H), int(b[2] * W), int(b[3] * H)]
              for b, _ in boxes]
        cov, bands = _measure_coverage(gray, bg, px)
        retried = False

        # ---- one stricter retry, naming the actual miss ------------------
        if cov < COVERAGE_TARGET:
            retried = True
            where = ""
            if bands:
                where = (" — the largest gap runs from "
                         f"{bands[0][0] / H:.0%} to {bands[0][1] / H:.0%} "
                         "down the page")
            if progress:
                progress("split", f"page {pi}: {cov:.0%} coverage — retrying")
            boxes2 = _ask(SPLIT_RETRY_SUFFIX.format(
                pct=int(round((1 - cov) * 100)), bands=where))
            px2 = [[int(b[0] * W), int(b[1] * H), int(b[2] * W), int(b[3] * H)]
                   for b, _ in boxes2]
            cov2, bands2 = _measure_coverage(gray, bg, px2)
            if cov2 > cov:
                boxes, px, cov, bands = boxes2, px2, cov2, bands2

        # ---- geometric recovery of whatever is still missed --------------
        recovered = 0
        if cov < COVERAGE_TARGET and bands:
            extra = _recover_bands(gray, bg, bands, W)
            recovered = len(extra)
            px.extend(extra)
            boxes.extend(([x0 / W, y0 / H, x1 / W, y1 / H], "art")
                         for x0, y0, x1, y1 in extra)
            cov, _ = _measure_coverage(gray, bg, px)
            if progress and recovered:
                progress("split", f"page {pi}: recovered {recovered} missed "
                                  f"band(s), now {cov:.0%}")

        # ---- tall handling, then write --------------------------------
        order = sorted(zip(px, [k for _, k in boxes]),
                       key=lambda t: (t[0][1], t[0][0]))
        final = []
        for bx, kind in order:
            for piece in _split_tall(gray, bg, bx, W):
                final.append((piece, kind))

        stem = f"page{pi:03d}"
        with Image.open(path) as im:
            im = im.convert("RGB")
            idx = 0
            for bx, kind in final:
                x0, y0, x1, y1 = [int(v) for v in bx]
                if x1 - x0 < MIN_PANEL_PX or y1 - y0 < MIN_PANEL_PX:
                    continue
                crop_gray = gray[y0:y1, x0:x1]
                # A blank or near-blank crop is a gutter sliver, not a panel.
                try:
                    if SP._is_blank_crop(crop_gray, bg):
                        blanks += 1
                        continue
                except Exception:
                    pass
                idx += 1
                out = f"{stem}_panel_{idx:03d}.png"
                im.crop((x0, y0, x1, y1)).save(os.path.join(crops_dir, out))
                made.append({"file": out, "kind": kind, "page": stem,
                             "box": [x0 / W, y0 / H, x1 / W, y1 / H]})

        page_stats.append({"page": stem, "coverage": round(cov, 3),
                           "panels": idx, "retried": retried,
                           "recovered_bands": recovered})

    if not made:
        raise LabError("Claude found no usable panels on any page")

    covs = [p["coverage"] for p in page_stats] or [1.0]
    return made, tally.stats(
        pages=len(pages), panels=len(made), blanks_dropped=blanks,
        coverage_min=round(min(covs), 3),
        coverage_mean=round(sum(covs) / len(covs), 3),
        pages_below_target=sum(1 for c in covs if c < COVERAGE_TARGET),
        retried_pages=sum(1 for p in page_stats if p["retried"]),
        recovered_pages=sum(1 for p in page_stats if p["recovered_bands"]),
        per_page=page_stats)


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


def _cached_stage(pdir, name, fresh, compute):
    """Run a paid stage once, then reuse its result.

    `read` already resumed (it skips panels present in descriptions.json), but
    every stage after it — map, script, critique, revise — re-ran from scratch,
    so a run killed during `script` re-paid for `map` and `script` on the next
    attempt. The read stage is the expensive one, which is why this went
    unnoticed, but "cheaper than the expensive stage" is not the same as free.

    Returns (value, cost_stats). A cache hit reports zero cost because this run
    genuinely did not spend anything on it — the spend is recorded in the run
    that actually paid.
    """
    if not fresh:
        hit = CP.read(pdir, name, None)
        if hit is not None:
            return hit, {"cost_usd": 0.0, "calls": 0, "cached": True}
    value, st = compute()
    CP._write(pdir, name, value)
    return value, st


def run_lab(url, splitter="claude", model=None, progress=None, job_id="lab",
            voice=True, fresh=False):
    """Build a whole chapter, independently, with Claude making the judgement
    calls and deterministic code making the decisions that have exact answers.

    STAGE ORDER MATTERS AND CHANGED. The crop is now chosen LAST, after the
    script exists and after every line has been placed, because a crop is a
    decision about what the viewer must see for a particular line to land — and
    that is unanswerable before the line has been written. The first version
    chose crops during the read pass, which made it structurally impossible for
    the framing to serve the narration.
    """
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

    # FRESH MUST MEAN FRESH. Clearing only pages and crops left the DERIVED
    # artifacts in place — and the read stage resumes from whatever
    # descriptions.json already holds. So a re-run after the read contract
    # changed would silently skip every panel as "already read" and quietly
    # reuse the old reads, which is the exact opposite of what fresh means and
    # would make any before/after comparison meaningless.
    if fresh:
        for rel in ("descriptions.json", "chapter_map.json", "units.json",
                    "script.json", "manifest.json"):
            try:
                os.remove(os.path.join(CP.out_dir(pdir), rel))
            except FileNotFoundError:
                pass
        for rel in ("descriptions.json", "script.json", "script.txt",
                    "segments.json", "panels.json", "review.json",
                    "storyboard.json"):
            try:
                os.remove(os.path.join(pdir, rel))
            except FileNotFoundError:
                pass

    man = manifest(pdir) or {}
    man.update({"url": url, "splitter": splitter, "model": model,
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "running", "error": None, "lab": True,
                "pipeline": "claude+",
                "cost_usd": man.get("cost_usd", 0.0),
                "calls": man.get("calls", 0),
                "passes": man.get("passes", {}),
                "diagnostics": man.get("diagnostics", {})})
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

        # ---- 2. split (SWITCHABLE, coverage-validated) -----------------
        if fresh or not os.listdir(crops):
            shutil.rmtree(crops, ignore_errors=True)
            os.makedirs(crops, exist_ok=True)
            if splitter == "claude":
                made, st = split_with_claude(pages, crops, model=model,
                                             progress=_prog)
                # If Claude cutting is still short of target after its retry
                # AND its geometric recovery, the honest move is to fall back
                # to the trained detector rather than build a chapter with
                # holes in it. The fallback is recorded, never silent.
                if st.get("coverage_mean", 1.0) < COVERAGE_TARGET:
                    _prog("split", f"Claude coverage {st['coverage_mean']:.0%} "
                                   f"below target — falling back to YOLO")
                    man["diagnostics"]["split_fallback"] = {
                        "from": "claude", "to": "yolo",
                        "claude_coverage_mean": st.get("coverage_mean"),
                        "target": COVERAGE_TARGET}
                    _absorb("split_claude_attempt", st)
                    shutil.rmtree(crops, ignore_errors=True)
                    os.makedirs(crops, exist_ok=True)
                    made, st = split_with_yolo(pages, crops, job_id=job_id,
                                               progress=_prog)
            else:
                made, st = split_with_yolo(pages, crops, job_id=job_id,
                                           progress=_prog)
            _absorb("split", st)
            with open(os.path.join(pdir, "panels.json"), "w",
                      encoding="utf-8") as f:
                json.dump(made, f, indent=2)
            _prog("split", f"{len(made)} panels")

        # ---- 3. read (Claude+ contract) --------------------------------
        panel_files = sorted(f for f in os.listdir(crops) if f.endswith(".png"))
        if not panel_files:
            raise LabError("no panel crops to read")
        from PIL import Image
        panels = []
        for f in panel_files:
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

        # The read stage is the long one and the one most likely to be cut
        # short by a cap or a restart. Its cost is banked after every batch, so
        # a run that dies half way still reports what it actually spent.
        read_tally = CP._Tally()
        banked = {"cost": 0.0, "calls": 0}

        def _ckpt(partial):
            merged = prior + [d for d in partial if d["panel_id"] not in done]
            merged.sort(key=lambda r: r.get("n", 0))
            CP._write(pdir, "descriptions.json", merged)
            man["cost_usd"] = round(
                man["cost_usd"] - banked["cost"] + read_tally.cost, 6)
            man["calls"] += read_tally.calls - banked["calls"]
            banked["cost"], banked["calls"] = read_tally.cost, read_tally.calls
            _save_manifest(pdir, man)

        _prog("read", f"Claude reading {len(panels) - len(done)} panels")
        fresh_descs, st = PLUS.describe_plus(
            pdir, panels, model=model, progress=lambda m: _prog("read", m),
            on_batch=_ckpt, skip=done, tally=read_tally)
        # Already banked incrementally above; do not count it twice.
        st = dict(st, cost_usd=0.0, calls=0, banked_incrementally=True)
        _ckpt(fresh_descs)
        _absorb("read", st)
        descs = CP.read(pdir, "descriptions.json", [])
        if not descs:
            raise LabError("Claude produced no panel readings")
        shutil.copyfile(os.path.join(CP.out_dir(pdir), "descriptions.json"),
                        os.path.join(pdir, "descriptions.json"))

        # ---- 4. chapter map --------------------------------------------
        _prog("map", "Claude mapping the chapter")
        cmap, st = _cached_stage(
            pdir, "chapter_map.json", fresh,
            lambda: PLUS.chapter_map(descs, model=model,
                                     progress=lambda m: _prog("map", m)))
        if st.get("cached"):
            _prog("map", "chapter map reused from the earlier run")
        _absorb("map", st)

        # ---- 5. script (budgeted, scene-aware) -------------------------
        units, st = _cached_stage(
            pdir, "script_units.json", fresh,
            lambda: PLUS.script_plus(descs, cmap, model=model,
                                     progress=lambda m: _prog("script", m)))
        if st.get("cached"):
            _prog("script", "narration reused from the earlier run")
        _absorb("script", st)
        if not units:
            raise LabError("Claude wrote no narration")
        man["diagnostics"]["dense_allowances"] = st.get("allowances", [])

        # ---- 6. critique + revise --------------------------------------
        def _critique_and_revise():
            iss, cst = PLUS.critique(units, descs, model=model,
                                     progress=lambda m: _prog("critique", m))
            _absorb("critique", cst)
            # Under-spend is arithmetic the pipeline already has, so it is
            # raised in code rather than asked of the reviewer.
            iss = iss + PLUS.underspend_issues(units)
            rev, rst = PLUS.revise(units, iss, descs, cmap, model=model,
                                   progress=lambda m: _prog("critique", m))
            return {"units": rev, "issues": iss}, rst

        # Cached as ONE unit: revised lines without the critique that produced
        # them is not a state worth resuming from.
        blob, st = _cached_stage(pdir, "script_revised.json", fresh,
                                 _critique_and_revise)
        if st.get("cached"):
            _prog("critique", "critique + revision reused from the earlier run")
        _absorb("revise", st)
        units = blob["units"]
        issues = blob["issues"]
        man["diagnostics"]["critique_issues"] = issues

        with open(os.path.join(pdir, "script.json"), "w", encoding="utf-8") as f:
            json.dump([{"scene_id": u["scene_id"], "text": u["text"],
                        "panel_ids": u.get("panel_ids", [])} for u in units],
                      f, indent=2)
        with open(os.path.join(pdir, "script.txt"), "w", encoding="utf-8") as f:
            f.write("\n\n".join(u["text"] for u in units))

        # ---- 7. beats (SHARED deterministic segmenter) -----------------
        beats = beat_segmenter.segment_beats_scenes(
            [{"scene_id": u["scene_id"], "text": u["text"],
              "panel_ids": u.get("panel_ids", [])} for u in units])
        if not beats:
            raise LabError("the script produced no beats")
        _prog("beats", f"{len(beats)} narration beats")

        # ---- 8. voice (SHARED, unchanged) ------------------------------
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

        # ---- 9. place (DETERMINISTIC, monotonic) -----------------------
        _prog("place", f"placing {len(beats)} beats on {len(descs)} panels")
        # Measure bubble coverage before placing, so "is this a picture or a
        # page of dialogue?" is answered from the image, not inferred.
        n_bf = annotate_bubble_frac(descs, crops)
        _prog("place", f"measured bubble coverage on {n_bf} panels")
        idx_of = {d["panel_id"]: i for i, d in enumerate(descs)}
        allowed = {}
        for i, b in enumerate(beats):
            ok = {idx_of[p] for p in (b.get("panel_ids") or []) if p in idx_of}
            if ok:
                allowed[i] = ok
        assigns, diag = PLACE.place(beats, descs, allowed=allowed,
                                    progress=lambda m: _prog("place", m))
        if not assigns:
            raise LabError("placement produced no assignments")
        assigns, repairs = PLACE.enforce_monotonic(assigns)

        # VISUAL PROGRESSION. A unit that legitimately spans several distinct
        # panels must still move through them instead of holding one frame for
        # the whole line — which is what "folded" on the board means.
        # The units the script stage produced — each already carries the
        # panel_ids its scene owns, which is exactly the set a beat may show.
        unit_panels = {}
        for u in units:
            unit_panels[u["scene_id"]] = [
                idx_of[pid] for pid in (u.get("panel_ids") or [])
                if pid in idx_of]
        slots, prog = PLACE.expand_units(assigns, beats, descs, unit_panels)
        man["diagnostics"]["progression"] = prog
        diag["order_repairs"] = repairs
        diag["hold_cap_breaches"] = PLACE.cap_holds(assigns, beats)
        diag["ambiguous"] = PLACE.ambiguous_pairs(beats, descs, assigns)
        man["diagnostics"]["placement"] = diag
        _absorb("place", {"calls": 0, "cost_usd": 0.0, "prompt_tokens": 0,
                          "output_tokens": 0,
                          "distinct_panels": diag.get("distinct_panels"),
                          "provenance_escapes": len(diag.get("provenance_escapes", [])),
                          "order_repairs": len(repairs)})

        # ---- 10. crop (AFTER the script and the placement) -------------
        # Every panel that actually gets screen time is framed — including the
        # ones recovered by the progression pass, which would otherwise show at
        # full frame purely because nothing asked about them.
        first_slot = {}
        for k, sl in enumerate(slots):
            pid = descs[sl["panel_index"]]["panel_id"]
            if pid not in first_slot:
                first_slot[pid] = k
        placements = []
        for pid, k in sorted(first_slot.items(), key=lambda kv: kv[1]):
            d = descs[idx_of[pid]]
            placements.append({
                "panel": d,
                "line": beats[slots[k]["beat_index"]]["text"],
                "prev": beats[slots[k - 1]["beat_index"]]["text"][:160]
                        if k > 0 else "",
                "next": beats[slots[k + 1]["beat_index"]]["text"][:160]
                        if k + 1 < len(slots) else "",
            })
        crops_by_pid, st = PLUS.plan_crops(
            pdir, placements, model=model, progress=lambda m: _prog("crop", m))
        _absorb("crop", st)
        man["diagnostics"]["crop_rejected"] = st.get("rejected", [])

        # ---- 11. segments ----------------------------------------------
        _prog("segment", "building render segments")
        segs = _build_shots(crops, descs, beats, slots, crops_by_pid,
                            build_segments, audio_dir=audio)
        with open(os.path.join(pdir, "segments.json"), "w",
                  encoding="utf-8") as f:
            json.dump(segs, f, indent=2)

        # ---- 11b. the build must not declare itself ready if it is not ----
        # `ready` used to mean only "segments.json exists", which is how
        # 353-lab-yolo came out of the oven already unrenderable: beat 6 was
        # 9.144s of audio inside a 4.699s window. A chapter that cannot render
        # is not a finished chapter, so the build checks its OWN timeline,
        # repairs what is mechanically repairable, and reports what is left
        # instead of handing the operator a green light and a render failure.
        _prog("segment", "validating the timeline")
        man["timeline"] = _validate_own_timeline(pdir)

        series, chapter = ingest.parse_series_chapter(url)
        meta = {
            "id": lab_id(url, splitter), "url": url,
            "crops": crops, "audio": audio,
            "descriptions": os.path.join(pdir, "descriptions.json"),
            "n_segments": len(segs),
            "duration": round(segs[-1]["end"], 1) if segs else 0,
            "series": ingest.to_title_case(ingest.clean_series_slug(series)),
            "chapter": ingest.to_title_case(chapter),
            "match_method": f"claude+ lab ({splitter} split, DP placement)",
            "lab": True, "splitter": splitter, "pipeline": "claude+",
            "lab_note": ("Independent experiment. Panels, descriptions, script "
                         "and framing by Claude; placement by deterministic DP; "
                         "audio by the standard TTS path. Panel cutting by "
                         + ("Claude with coverage validation"
                            if splitter == "claude" else "YOLO") + "."),
        }
        with open(os.path.join(pdir, "project.json"), "w",
                  encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        man.update({"status": "ok", "project": meta["id"],
                    "panels": len(descs), "units": len(units),
                    "beats": len(beats), "segments": len(segs),
                    "duration": meta["duration"],
                    "elapsed_sec": round(time.time() - started, 1)})
        _save_manifest(pdir, man)
        return man

    except (LabError, CP.PipelineError, PLUS.PlusError,
            validator.ValidatorError, usage.UsageCapExceeded) as e:
        man.update({"status": "error", "error": str(e),
                    "elapsed_sec": round(time.time() - started, 1)})
        _save_manifest(pdir, man)
        return man


def _audio_dur(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], text=True).strip()
    return round(float(out), 3)


def _extract_range(src, t0, t1, out):
    """Write src[t0:t1] to out. Re-encodes so the cut lands where asked."""
    subprocess.run(["ffmpeg", "-y", "-ss", f"{t0:.3f}", "-t", f"{max(t1 - t0, 0.05):.3f}",
                    "-i", src, "-q:a", "4", out],
                   check=True, capture_output=True)


def _slice_shared_beats(shots, beats, audio_dir):
    """Cut a beat's mp3 when its sentence is spread over several panels.

    THE BUG THIS FIXES: `expand_units` deliberately spreads one narration line
    across several panels for visual progression, and each resulting shot got
    its own narrow window but kept the SAME beat index. With no explicit file
    the renderer falls back to `beat_<index>.mp3` — the whole sentence — so a
    5.5s file was scheduled inside a 5.099s window and the render was refused.
    Measured on i-am-the-fated-villain_352: 52 of 79 segments, because a
    multi-panel unit is the normal case, not an edge case.

    Production already solved this for hand edits (`storyboard_edit._slice_mp3`
    writes an explicit per-part file); the lab build simply never did it.

    Every shot of a multi-shot beat gets an explicit file — including any
    degenerate one — because a single missing file reinstates the whole-file
    fallback and with it the bug.
    """
    by_beat = {}
    for sh in shots:
        by_beat.setdefault(sh["index"], []).append(sh)

    sliced = 0
    for idx, group in by_beat.items():
        if len(group) < 2:
            continue                       # one panel, whole file is correct
        src = os.path.join(audio_dir, f"beat_{idx:03d}.mp3")
        if not os.path.exists(src):
            continue
        try:
            dur = _audio_dur(src)
        except Exception:
            continue
        group.sort(key=lambda x: x["start"])
        base = float(beats[idx]["start"])
        for i, sh in enumerate(group):
            t0 = max(0.0, float(sh["start"]) - base)
            # The last part runs to the end of the audio: the final shot's
            # window is snapped to the NEXT scene's start, which may sit past
            # the sentence, and trimming there would clip the last words.
            t1 = (dur if i == len(group) - 1
                  else max(t0 + 0.05, float(group[i + 1]["start"]) - base))
            t0, t1 = min(t0, dur), min(t1, dur)
            if t1 <= t0:
                t0, t1 = max(0.0, dur - 0.05), dur
            name = f"beat_{idx:03d}_p{i:02d}.mp3"
            try:
                _extract_range(src, t0, t1, os.path.join(audio_dir, name))
            except Exception:
                continue
            sh["beat_file"] = name
            sliced += 1
    return sliced


def annotate_bubble_frac(descs, crops_dir):
    """Measure how much of each panel is speech bubble, from the image itself.

    This is the BACKEND/FRONTEND split made measurable: the placer uses it to
    keep a panel that is mostly dialogue box off the screen, while its OCR
    still feeds the script. Reuses production's own `_detect_bubbles`, so the
    two pipelines agree on what a bubble is.

    A panel that cannot be read is left unannotated rather than guessed at —
    `is_text_surface` then falls back to the reader's `subject_type`.
    """
    try:
        sys.path.insert(0, os.path.join(ROOT, "panel-split"))
        import split_panels as SP
        import numpy as np
        from PIL import Image
    except Exception:
        return 0

    done = 0
    for d in descs:
        path = os.path.join(crops_dir, d.get("file") or f"{d['panel_id']}.png")
        if not os.path.exists(path):
            continue
        try:
            g = np.array(Image.open(path).convert("L"))
            h, w = g.shape
            bg = SP._estimate_background_color(g)
            area = 0
            for b in SP._detect_bubbles(g, bg) or []:
                if isinstance(b, (list, tuple)) and len(b) >= 4:
                    area += max(0, b[2] - b[0]) * max(0, b[3] - b[1])
            d["bubble_frac"] = round(area / max(w * h, 1), 4)
            done += 1
        except Exception:
            continue
    return done


def _validate_own_timeline(pdir):
    """Check, repair, re-check. Returns what the operator needs to know.

    The repair is the same `repair_shared_beats` that took 352-lab-claude from
    52 errors to 20 — it slices audio that several segments were each claiming
    in full. Errors that survive it are a different fault (a single panel whose
    window is shorter than its own sentence) and need a pacing decision, so
    they are REPORTED rather than silently papered over.
    """
    out = {"errors": None, "repaired": 0, "checked": False}
    try:
        import storyboard_edit as SE
    except Exception:
        return out
    def _count():
        try:
            return len(SE.validate_timeline(pdir).get("errors") or [])
        except Exception:
            return None
    before = _count()
    if before is None:
        return out
    out["checked"] = True
    out["errors_before"] = before
    if before:
        try:
            out["repaired"] = len(SE.repair_shared_beats(pdir) or [])
        except Exception:
            out["repaired"] = 0
    out["errors"] = _count()
    return out


def _build_shots(crops, descs, beats, slots, crops_by_pid, build_segments,
                 audio_dir=None):
    """One shot per beat, carrying the crop chosen for its panel.

    Unlike the first version there is no folding maths here: the DP already
    decided which panel each beat plays over, so a shot is simply that pairing.
    Ends are snapped to the next shot's start — the same exact-tiling rule
    production uses, without which the pauses the TTS rhythm inserts between
    scenes render as black frames.
    """
    shots = []
    for a in slots:
        b = beats[a["beat_index"]]
        d = descs[a["panel_index"]]
        pid = d["panel_id"]
        cr = crops_by_pid.get(pid) or {}
        shots.append({
            # The SLOT's window, not the beat's: a unit spanning several panels
            # gives each one a slice of the shared narration window.
            "index": b["index"], "start": float(a["start"]),
            "end": float(a["end"]), "beat_text": b["text"],
            "panel_id": pid,
            "panel_file": os.path.join(crops, d.get("file") or f"{pid}.png"),
            "width": d.get("width"), "height": d.get("height"),
            "crop_bbox_norm": cr.get("crop_bbox_norm") or [0.0, 0.0, 1.0, 1.0],
            "focus_source": "claude+",
            "focus_reason": cr.get("focus_reason", "full panel"),
            "focus_confidence": cr.get("focus_confidence", 1.0),
        })
    if not shots:
        raise LabError("no shots to build")
    shots.sort(key=lambda s: s["start"])
    for i in range(len(shots) - 1):
        shots[i]["end"] = shots[i + 1]["start"]
    for sh in shots:
        sh["dur"] = round(sh["end"] - sh["start"], 3)
    # Slice AFTER the ends are snapped — the snapped window is the one the
    # renderer will check the audio against.
    if audio_dir:
        _slice_shared_beats(shots, beats, audio_dir)
    return build_segments(shots)
