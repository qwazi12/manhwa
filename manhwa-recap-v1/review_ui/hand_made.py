"""A chapter written by hand (owner, 2026-11-18: "can't you handle everything so
the only thing we need to pay for is voice?").

The studio downloads and cuts the chapter itself, without the paid check of
the cuts. Whoever writes the chapter (a person, or an agent looking at the
panels) then hands over what the paid steps would have produced:

  panels     what each panel shows and says        (describe, about 25 cents)
  lines      the spoken lines, each on its panel   (script about 20 cents, matching)
  subject_box / turn_cw on a panel, optional       (framing, about 19 cents)
  packaging  the title/description/tags answer     (packaging, about 8 cents)

The studio records the voice (paid), builds the board and renders as for any
chapter. `hand.json` in the chapter folder is the whole hand-over. A chapter
that has one is voice only: usage.gate refuses every paid call labelled with
it that is not voice (usage.voice_only).

Routes: POST /api/hand/prepare, GET /api/hand/<chapter>, POST /api/hand/<chapter>,
DELETE /api/hand/<chapter> (server.py). No model call is made in this file.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
import time

NAME = "hand.json"
PREP = "hand_prep.json"
VERSION = 1
MIN_LINES = 5
MAX_LINE_WORDS = 45          # beat_segmenter.MAX_BEAT_WORDS: one line is one shot
MAX_TEXT = 4000              # characters of description or dialogue for one panel
PREP_STALE_S = 20 * 60       # a "preparing" record older than this was cut off by a restart
_IMG = (".png", ".jpg", ".jpeg", ".webp")

GUIDE = """Write a chapter by hand. Only the voice is paid.

1. POST /api/hand/prepare {"url": "<chapter link>", "variant": ""}
   The studio downloads the pages and cuts them into panels (free).
2. GET /api/hand/<chapter> until "state" is "ready". "panels" lists every panel;
   its picture is at /api/hand/<chapter>/panel/<panel_id>.
3. Look at every panel, in order, then POST /api/hand/<chapter> with:
   {"panels": [{"panel_id": "page001_panel_001",
                "visual_description": "who and what the picture shows, one or two sentences",
                "ocr_text": "the words printed in the panel, as printed",
                "subject_box": [x0, y0, x1, y1],      (optional; fractions of the picture, without balloons)
                "turn_cw": 90}],                      (optional; a panel drawn sideways)
    "lines": [{"panel": "page001_panel_003", "text": "one spoken line", "scene": 1}],
    "packaging": {...}}                               (optional; see below)
   Every panel needs an entry (an empty or bubble-only panel still gets a
   description saying so). Every line names the one panel shown while it is
   spoken; lines play in the order given; a panel may carry several lines or
   none. "scene" changes when the place or time changes (a longer breath is
   left there). One line is at most 45 words. Names: only those printed in
   the chapter or listed under "cast".
   "packaging": {"titles": [{"text": "hook only", "why": "...", "recommended": true}],
                 "summary": "", "description": "", "description_short": "", "tags": [], "hashtags": [],
                 "tease": "max 180 characters, no spoiler", "thumbnail_text": "MAX 4 WORDS",
                 "dramatic_beat": "", "new_entities": []}
4. The answer names the job. The chapter then appears with the others; the
   board, the render and the review are the usual ones.
Send step 3 again to replace the script (unchanged lines are not recorded twice).
DELETE /api/hand/<chapter> gives the chapter back to the paid pipeline."""


class HandError(ValueError):
    pass


def _natural(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _digest(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _write_json(path, data, indent=1):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load(pdir):
    """The hand-over, or None when the chapter has none. A file that is there
    but cannot be read is an error: treating it as absent would send the
    chapter through the paid steps."""
    p = os.path.join(pdir, NAME)
    if not os.path.exists(p):
        return None
    data = _read_json(p)
    if not isinstance(data, dict) or data.get("version") != VERSION or not data.get("lines"):
        raise HandError(f"{NAME} of this chapter cannot be read; send the script again (POST /api/hand/<chapter>)")
    return data


def is_hand(pdir):
    return os.path.exists(os.path.join(pdir, NAME))


def panel_listing(pdir):
    """Every panel cut for this chapter, in reading order."""
    crops = os.path.join(pdir, "crops")
    out = []
    try:
        names = sorted((f for f in os.listdir(crops) if f.lower().endswith(".png")), key=_natural)
    except OSError:
        return out
    from PIL import Image
    for f in names:
        p = os.path.join(crops, f)
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:
            w = h = 0
        out.append({"panel_id": os.path.splitext(f)[0], "file": f, "width": w, "height": h,
                    "sha256": _digest(p)})
    return out


def _box(v):
    try:
        b = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    if len(b) != 4 or not all(0.0 <= x <= 1.0 for x in b) or b[2] - b[0] < 0.05 or b[3] - b[1] < 0.05:
        return None
    return [round(x, 4) for x in b]


def validate(pdir, body):
    """(record ready to save, problems). Nothing is saved while there is a problem."""
    import narration_guard
    problems = []
    listing = panel_listing(pdir)
    on_disk = {p["panel_id"]: p for p in listing}
    if not on_disk:
        return None, ["this chapter has no panels yet: prepare it first (POST /api/hand/prepare)"]
    body = body if isinstance(body, dict) else {}
    panels, seen = [], set()
    for i, p in enumerate(body.get("panels") or []):
        pid = str((p or {}).get("panel_id") or "") if isinstance(p, dict) else ""
        if pid not in on_disk:
            problems.append(f"panels[{i}]: no panel '{pid}' in this chapter")
            continue
        if pid in seen:
            problems.append(f"panels[{i}]: '{pid}' is described twice")
            continue
        seen.add(pid)
        desc = " ".join(str(p.get("visual_description") or "").split())
        ocr = str(p.get("ocr_text") or "").strip()
        if not desc:
            problems.append(f"{pid}: no visual_description (say what the picture shows, even when it is only a balloon)")
        if len(desc) > MAX_TEXT or len(ocr) > MAX_TEXT:
            problems.append(f"{pid}: description or text is longer than {MAX_TEXT} characters")
        rec = {"panel_id": pid, "visual_description": desc, "ocr_text": ocr,
               "sha256": on_disk[pid]["sha256"]}
        if p.get("subject_box") is not None:
            box = _box(p["subject_box"])
            if box is None:
                problems.append(f"{pid}: subject_box must be [x0, y0, x1, y1], fractions between 0 and 1")
            else:
                rec["subject_box"] = box
        if p.get("turn_cw"):
            if p["turn_cw"] not in (90, 180, 270):
                problems.append(f"{pid}: turn_cw must be 90, 180 or 270")
            else:
                rec["turn_cw"] = int(p["turn_cw"])
        panels.append(rec)
    missing = [pid for pid in on_disk if pid not in seen]
    if missing:
        problems.append(f"{len(missing)} panel(s) have no description: " + ", ".join(missing[:12])
                        + (" …" if len(missing) > 12 else ""))
    lines = []
    for i, ln in enumerate(body.get("lines") or []):
        ln = ln if isinstance(ln, dict) else {}
        pid, text = str(ln.get("panel") or ""), " ".join(str(ln.get("text") or "").split())
        if pid not in on_disk:
            problems.append(f"lines[{i}]: no panel '{pid}' in this chapter")
        elif not on_disk[pid]["width"] or not on_disk[pid]["height"]:
            problems.append(f"lines[{i}]: the picture of '{pid}' cannot be opened")
        if not text:
            problems.append(f"lines[{i}]: no text")
        elif len(text.split()) > MAX_LINE_WORDS:
            problems.append(f"lines[{i}]: {len(text.split())} words; one line is at most {MAX_LINE_WORDS} (split it)")
        elif narration_guard.is_assistant_text(text):
            problems.append(f"lines[{i}]: reads like a note from the writer, not narration: {text[:60]!r}")
        try:
            scene = int(ln.get("scene") or 0)
        except (TypeError, ValueError):
            scene = 0
        lines.append({"panel": pid, "text": text, "scene": scene})
    if len(lines) < MIN_LINES:
        problems.append(f"{len(lines)} line(s): a chapter needs at least {MIN_LINES}")
    pk = body.get("packaging")
    if pk is not None and not isinstance(pk, dict):
        problems.append("packaging must be an object")
        pk = None
    if problems:
        return None, problems
    rec = {"version": VERSION, "voice_only": True, "at": time.time(),
           "by": str(body.get("by") or "")[:80], "panels": panels, "lines": lines}
    if pk:
        rec["packaging"] = pk
    return rec, []


def save(pdir, rec):
    """Write hand.json; the one it replaces is kept in the chapter's _revisions."""
    p = os.path.join(pdir, NAME)
    if os.path.exists(p):
        keep = os.path.join(pdir, "_revisions")
        os.makedirs(keep, exist_ok=True)
        shutil.copy2(p, os.path.join(keep, "hand-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + ".json"))
    _write_json(p, rec)


def remove(pdir):
    """Give the chapter back to the paid pipeline. The hand-over is kept in _revisions."""
    p = os.path.join(pdir, NAME)
    if not os.path.exists(p):
        return False
    keep = os.path.join(pdir, "_revisions")
    os.makedirs(keep, exist_ok=True)
    os.replace(p, os.path.join(keep, "hand-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-removed.json"))
    return True


def source_problem(pdir, hand):
    """Why the panels on disk are not the ones the hand-over was written about, or None."""
    now = {p["panel_id"]: p["sha256"] for p in panel_listing(pdir)}
    then = {p["panel_id"]: p.get("sha256") for p in hand.get("panels") or []}
    if not now:
        return "the chapter's panels are missing; prepare it again, then send the script again"
    changed = [pid for pid in then if now.get(pid) != then[pid]]
    extra = [pid for pid in now if pid not in then]
    if changed or extra:
        return (f"the panels changed after the script was written ({len(changed)} different, {len(extra)} new); "
                "look at them again and send the script again")
    return None


# ------------------------------------------------------------ pipeline steps
def write_descriptions(pdir, hand, desc_path):
    """descriptions.json from the hand-over, in the describe step's own format."""
    listing = {p["panel_id"]: p for p in panel_listing(pdir)}
    out = []
    for p in hand["panels"]:
        info = listing[p["panel_id"]]
        w, h = info["width"], info["height"]
        ocr = p.get("ocr_text") or ""
        out.append({"panel_id": p["panel_id"], "file": info["file"], "width": w, "height": h,
                    "bbox": [0, 0, w, h], "ocr_text": ocr,
                    "visual_description": p.get("visual_description") or "",
                    "lines": ([{"text": ocr, "type": "speech", "speaker": None,
                                "speaker_basis": "hand", "lang": "en"}] if ocr else []),
                    "source": "hand", "ok": bool(w and h),
                    "generation": {"version": 1, "inputs": {"source_sha256": info["sha256"], "engine": "hand"}}})
    out.sort(key=lambda r: _natural(r["panel_id"]))
    _write_json(desc_path, out, indent=2)
    return len(out)


def scenes(hand):
    """The script.json shape narrate writes: one entry per spoken line, on one panel."""
    return [{"scene_id": ln.get("scene") or 0, "panel_ids": [ln["panel"]], "text": ln["text"],
             "silent": False, "source_line_refs": []} for ln in hand["lines"]]


def write_script(pdir, hand):
    """script.txt, script.json and the records the script step leaves beside them."""
    sc = scenes(hand)
    script = "\n".join(s["text"] for s in sc)
    with open(os.path.join(pdir, "script.txt"), "w", encoding="utf-8") as f:
        f.write(script)
    _write_json(os.path.join(pdir, "script.json"), sc)
    record = {"note": "written by hand"}
    _write_json(os.path.join(pdir, "direct_speech.json"), record)
    _write_json(os.path.join(pdir, "critique.json"),
                {"by": "hand", "note": "written by hand: the script editor did not run",
                 "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(), "checked_at": time.time()})
    return script, sc, record


def beats(sc):
    """One spoken line = one beat on its panel; nothing is split or merged."""
    return [{"index": i, "text": s["text"], "word_count": len(s["text"].split()),
             "scene_id": s["scene_id"], "panel_ids": list(s["panel_ids"]), "silent": False,
             "source_line_refs": []} for i, s in enumerate(sc)]


def assignments(beat_list, panels):
    """Each line on the panel it names (the matcher's output shape)."""
    where = {p.get("panel_id"): i for i, p in enumerate(panels)}
    out, prev = [], None
    for b in beat_list:
        pid = (b.get("panel_ids") or [None])[0]
        if pid not in where:
            raise HandError(f"line {b['index']} names panel '{pid}', whose picture cannot be used")
        out.append({"beat_index": b["index"], "panel_index": where[pid], "score": 1.0, "held": pid == prev})
        prev = pid
    return out


def seed_framing(pdir, hand, crops):
    """Put the hand-over's subject boxes and turns where auto_framing looks
    for its answers, so its own rules place the shot and no question is asked."""
    import auto_framing
    path = os.path.join(pdir, "auto_framing_cache.json")
    cache = auto_framing._load(path)
    n = 0
    for p in hand["panels"]:
        img = os.path.join(crops, p["panel_id"] + ".png")
        if not os.path.exists(img):
            continue
        if p.get("subject_box"):
            cache[auto_framing._key(img, "subject")] = {"ok": True, "subject_box": p["subject_box"],
                                                        "subject": "set by hand"}
            n += 1
        if p.get("turn_cw"):
            for i in range(len(auto_framing.ORDERS)):
                cache[auto_framing._key(img, f"turn{i}")] = {"ok": True,
                                                              "answer": [p["turn_cw"], 1.0, "set by hand"]}
    if n or any(p.get("turn_cw") for p in hand["panels"]):
        auto_framing._save(path, cache)
    return n


def seed_packaging(pdir, hand, seo_chapter_name="seo_chapter.json"):
    """The tease and thumbnail words a locked series' title package asks a
    model for, stored under the script they were written for (server._chapter_tease)."""
    pk = hand.get("packaging") or {}
    if not (pk.get("tease") and pk.get("thumbnail_text")):
        return False
    try:
        with open(os.path.join(pdir, "script.txt"), encoding="utf-8") as f:
            h = hashlib.sha1(f.read().encode()).hexdigest()[:16]
    except OSError:
        return False
    path = os.path.join(pdir, seo_chapter_name)
    rec = _read_json(path) or {}
    rec["hook_beat"] = {"dramatic_beat": str(pk.get("dramatic_beat") or "")[:200],
                        "tease": " ".join(str(pk["tease"]).split())[:200],
                        "new_entities": [str(x)[:40] for x in (pk.get("new_entities") or [])][:2],
                        "thumbnail_text": " ".join(str(pk["thumbnail_text"]).split()[:4]).upper()[:28],
                        "source": "written by hand", "script_hash": h, "at": time.time()}
    _write_json(path, rec)
    return True


def seo_answer(pdir):
    """The title/description/tags answer to use in place of the packaging model call, or None."""
    try:
        pk = (load(pdir) or {}).get("packaging") or {}
    except HandError:
        return None
    if not (pk.get("titles") and pk.get("description")):
        return None
    return json.dumps({k: pk.get(k) for k in ("titles", "summary", "description", "description_short",
                                              "tags", "hashtags", "reasoning", "detected") if pk.get(k)})


# ------------------------------------------------------------------ prepare
def prep_load(pdir, now=None):
    rec = _read_json(os.path.join(pdir, PREP))
    if not isinstance(rec, dict):
        return None
    if rec.get("state") == "preparing" and (now or time.time()) - float(rec.get("at") or 0) > PREP_STALE_S:
        rec = dict(rec, state="failed", error="it was cut off (the server restarted); prepare it again")
    return rec


def prep_save(pdir, rec):
    os.makedirs(pdir, exist_ok=True)
    _write_json(os.path.join(pdir, PREP), rec)


def prepare(url, pdir, slug="", say=None):
    """Download the chapter and cut it into panels, with no paid check of the
    cuts. The panels land in <chapter>/crops exactly as for a made chapter."""
    import generation_cache as cache
    import scraper
    import splitlab
    say = say or (lambda msg: None)
    os.makedirs(pdir, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".source-staging-", dir=pdir)
    pages, crops = os.path.join(staging, "pages"), os.path.join(staging, "crops")
    os.makedirs(pages)
    os.makedirs(crops)
    try:
        say("downloading the pages")
        imgs = scraper.download_chapter(url, pages)
        if not imgs:
            raise RuntimeError("no pages could be downloaded (blocked or a bad link)")
        warning = getattr(scraper, "LAST_WARNING", "") or ""
        say(f"{len(imgs)} pages; cutting them into panels")
        files = sorted(os.path.join(pages, f) for f in os.listdir(pages)
                       if os.path.splitext(f)[1].lower() in _IMG)
        n, stats = splitlab.split_into(files, crops, slug=slug, on_progress=say, vision=False)
        if not n:
            raise RuntimeError("the splitter produced no panels")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)      # nothing half-downloaded is left behind
        raise
    cache.promote_sources(pdir, staging)                # the previous cut, if any, goes to _revisions
    return {"n_pages": len(imgs), "n_panels": n, "scrape_warning": warning,
            "split_coverage": {"engine": "background-registration", "format": stats.get("format"),
                               "pages": stats.get("pages"), "panels": stats.get("panels"),
                               "refine": stats.get("refine") or {}, "vision": False}}
