"""Automatic orientation and subject positioning for panel shots.

Two vision questions, each cached by image content:
  * SUBJECT (every panel a shot uses): the box of the main subject, excluding
    balloons, captions, SFX lettering and blank gutters;
  * TURN (tall panels only, and only when no translated lettering already
    proves the panel upright): three copies of the panel (as source, 90° and
    270° clockwise) are compared side by side, asked twice in different
    orders. A turn counts only when both answers name the same copy with
    high confidence — this removed the position-bias false turns the single
    question produced on Chapter 34.

A pure policy (`decide`) turns the answers into framing. It is conservative
and always reversible through the existing board controls:
  * turns are SUGGESTED on the board by default (AUTO_TURN=suggest); set
    AUTO_TURN=apply to apply them automatically, AUTO_TURN=off to skip the
    turn question. On reviewed Chapter 34 the turn check found 1 of 2 sideways
    spreads with 1 false turn in 62 tall panels — useful as a suggestion,
    not yet safe to apply unreviewed;
  * a subject crop replaces ONLY a default full-panel fallback, never a
    reviewer edit or a planner close-up, and only when it removes a real
    amount of balloon/gutter space. On Chapter 34 it matched reviewer crops
    at mean IoU 0.69 versus 0.49 for the whole panel;
  * source pixels are never rewritten (panel_framing applies any turn).

AUTO_FRAMING=off disables both questions. See docs/FLAMINGO_AUTO_FRAMING.md.
"""
import hashlib
import io
import json
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont

import panel_framing

PROMPT_VERSION = "af2"
MODEL = os.environ.get("AUTO_FRAMING_MODEL", "gemini-3.8-flash")
TURN_MIN_CONF = float(os.environ.get("AUTO_TURN_MIN_CONF", 0.8))
TURN_CANDIDATE_AR = 1.8          # sideways spreads in a vertical scroll are tall
LETTERING_WORDS = 3              # this many Latin words in OCR => upright
CROP_MAX_AREA = 0.85             # must remove at least 15% of the frame
CROP_MIN_AREA = 0.15             # never shrink to a sliver
MARGIN = 0.04                    # breathing room around the subject
# On-screen size of a subject crop relative to the current framing. A fitted
# panel must get >=10% larger (every reviewed Chapter 34 crop of one did).
# A scrolling tall panel trades size for focus: reviewers accepted crops
# shown at 0.58-0.80x the scroll width, so only extreme slivers are refused.
MIN_GAIN = 1.10
SCROLL_MIN_GAIN = 0.55
FRAME_AR = 9 / 16                # output frame height/width
SEND_MAX_EDGE = 1024             # downscale before sending (cost)
SHEET_TILE = 448                 # one copy's box in the comparison sheet
ORDERS = ((0, 90, 270), (90, 270, 0))
WORKERS = int(os.environ.get("AUTO_FRAMING_WORKERS", 5))

# Shots whose current framing is a default, not a decision.
# A multi-beat punch-in is a fixed zoom, not a framing decision.
REPLACEABLE_SOURCES = {"fallback_full", "no_api_key_fallback", "multi_beat_punchin", None}

SUBJECT_PROMPT = """You are framing a webtoon (vertical-scroll comic) panel for a 16:9 recap video.
Give the box of the main visual subject a viewer should see: characters,
faces, the action or the key object/setting. EXCLUDE speech balloons, thought
balloons, caption boxes, large sound-effect lettering, blank/white gutters and
panel borders where possible. Coordinates are fractions of the image: x0,y0
top-left, x1,y1 bottom-right, all between 0 and 1.
Return ONLY JSON, no code fences:
{"subject_box": [x0, y0, x1, y1], "subject": "two to six words"}"""

TURN_PROMPT = """These are three copies of ONE webtoon panel, labelled A, B and C, each turned a different way.
Which copy shows the scene the right way up, as the artist intended?
Rules, in order of strength:
1. Translated dialogue or captions (English/Latin letters in balloons or boxes) are always horizontal and readable left-to-right in the correct copy. If one copy has such readable horizontal lettering, choose it.
2. Characters' heads are up and feet/ground/buildings are at the bottom; architecture stands vertically.
3. Most webtoon panels are drawn upright in a tall shape. Some wide scenes (a flying creature spread, a landscape) are drawn sideways to fit the vertical scroll; in those, Korean sound-effect lettering usually also reads correctly only after turning.
Return ONLY JSON: {"upright": "A"|"B"|"C", "confidence": 0.0-1.0, "evidence": "one short sentence"}"""


def mode():
    """'apply' | 'suggest' | 'off' for turns; positioning follows AUTO_FRAMING."""
    if os.environ.get("AUTO_FRAMING", "on").lower() in ("0", "off", "false"):
        return "off"
    m = os.environ.get("AUTO_TURN", "suggest").lower()
    return m if m in ("apply", "suggest", "off") else "suggest"


# ----------------------------------------------------------------- geometry
def rotate_box(box, degrees):
    """Map a normalized [x0,y0,x1,y1] box from the source image into the
    image turned `degrees` clockwise (the same turn panel_framing applies)."""
    x0, y0, x1, y1 = box
    if degrees == 0:
        return [x0, y0, x1, y1]
    if degrees == 90:            # (x, y) -> (1 - y, x)
        return [1 - y1, x0, 1 - y0, x1]
    if degrees == 180:           # (x, y) -> (1 - x, 1 - y)
        return [1 - x1, 1 - y1, 1 - x0, 1 - y0]
    if degrees == 270:           # (x, y) -> (y, 1 - x)
        return [y0, 1 - x1, y1, 1 - x0]
    raise ValueError("rotation must be 0, 90, 180 or 270")


def _clean_box(box):
    try:
        v = [float(b) for b in box]
    except (TypeError, ValueError):
        return None
    if len(v) != 4:
        return None
    if 1.0 < max(v) <= 1000.0:      # some answers use a 0-1000 scale
        v = [b / 1000.0 for b in v]
    v = [min(1.0, max(0.0, b)) for b in v]
    x0, x1 = sorted((v[0], v[2]))
    y0, y1 = sorted((v[1], v[3]))
    if x1 - x0 < 0.05 or y1 - y0 < 0.05:
        return None
    return [x0, y0, x1, y1]


def subject_crop(subject, width, height):
    """Crop (normalized, oriented image) that frames `subject` for 16:9.

    Portrait art keeps its full width and crops the vertical band around the
    subject — the shape reviewers chose on Chapter 34 — because a band of a
    tall panel fills far more of a landscape frame than the whole panel.
    Landscape/square art crops to the subject box itself. A band is grown
    vertically until it is no wider than the output frame, so it is never
    over-zoomed.
    """
    x0, y0, x1, y1 = subject
    x0, y0 = max(0.0, x0 - MARGIN), max(0.0, y0 - MARGIN)
    x1, y1 = min(1.0, x1 + MARGIN), min(1.0, y1 + MARGIN)
    if height > width * 1.05:
        x0, x1 = 0.0, 1.0
    ch, cw = (y1 - y0) * height, (x1 - x0) * width
    if cw and ch / cw < FRAME_AR:
        need = (FRAME_AR * cw / height - (y1 - y0)) / 2
        y0, y1 = y0 - need, y1 + need
        if y0 < 0:
            y1, y0 = min(1.0, y1 - y0), 0.0
        if y1 > 1:
            y0, y1 = max(0.0, y0 - (y1 - 1)), 1.0
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def display_scale(crop, width, height):
    """On-screen pixels per source pixel, mirroring the renderers
    (fast_render.render_frames / shot_planner.get_crop_layout, 1920x1080):
    a sub-crop is laid out by get_crop_layout; a whole tall panel scrolls at
    40% of the frame width; any other whole panel fits a card and is never
    upscaled."""
    if crop is not None:
        from shot_planner import get_crop_layout
        return get_crop_layout(crop, width, height)["w"] / ((crop[2] - crop[0]) * width)
    ar = height / width
    if ar >= panel_framing.TALL_AR:
        return 0.40 * 1920 / width
    mw, mh = (46, 97) if ar >= 1.25 else ((56, 93) if ar >= 0.85 else (68, 93))
    return min(1.0, 1920 * mw / 100 / width, 1080 * mh / 100 / height)


def has_lettering(ocr_text):
    """Translated balloons/captions are horizontal Latin text, which only
    reads in the upright picture — so their presence settles orientation."""
    return len(re.findall(r"[A-Za-z]{3,}", ocr_text or "")) >= LETTERING_WORDS


def turn_candidate(width, height, ocr_text=""):
    return height / width >= TURN_CANDIDATE_AR and not has_lettering(ocr_text)


# ------------------------------------------------------------------ answers
def _json(raw):
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.startswith("json") else text
    return json.loads(text)


def parse_subject(raw):
    try:
        data = _json(raw)
    except (AttributeError, TypeError, ValueError):
        return {"ok": False, "error": "unparseable subject answer"}
    return {"ok": True, "subject_box": _clean_box(data.get("subject_box")),
            "subject": str(data.get("subject", ""))[:80]}


def parse_turn(raw, order):
    """One comparison answer -> (clockwise degrees, confidence, evidence)."""
    try:
        data = _json(raw)
        deg = order["ABC".index(str(data["upright"]).strip().upper()[:1])]
        conf = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return deg, round(conf, 3), str(data.get("evidence", ""))[:300]


def combine_turns(answers):
    """Both orders must name the same turn, each with high confidence."""
    if len(answers) != len(ORDERS) or any(a is None for a in answers):
        return {"turn_cw": 0, "turn_confidence": 0.0, "turn_evidence": "turn check incomplete"}
    degs = {a[0] for a in answers}
    conf = min(a[1] for a in answers)
    if len(degs) != 1:
        return {"turn_cw": 0, "turn_confidence": 0.0,
                "turn_evidence": "the two comparisons disagreed"}
    deg = degs.pop()
    if deg and conf < TURN_MIN_CONF:
        return {"turn_cw": 0, "turn_confidence": conf,
                "turn_evidence": f"possible {deg}° turn below confidence {TURN_MIN_CONF}"}
    return {"turn_cw": deg, "turn_confidence": conf, "turn_evidence": answers[0][2]}


def decide(analysis, width, height, turn_mode="suggest"):
    """Pure policy: analysis + display size (EXIF-normalized) -> proposal.

    {"rotation_deg": applied turn, "suggested_turn": {...} | None,
     "crop_bbox_norm": crop in the oriented picture | None, "crop_reason"}
    """
    out = {"rotation_deg": 0, "suggested_turn": None, "rotation_reason": "",
           "crop_bbox_norm": None, "crop_reason": ""}
    analysis = analysis or {}
    turn = analysis.get("turn") or {}
    if turn.get("turn_cw"):
        sug = {"degrees": turn["turn_cw"], "confidence": turn["turn_confidence"],
               "evidence": turn.get("turn_evidence", "")}
        if turn_mode == "apply":
            out["rotation_deg"] = turn["turn_cw"]
            out["rotation_reason"] = (f"auto turn {turn['turn_cw']}° clockwise "
                                      f"({turn['turn_confidence']:.2f}): {sug['evidence']}")
        else:
            out["suggested_turn"] = sug
    deg = out["rotation_deg"]
    subject = analysis.get("subject") or {}
    box = subject.get("subject_box") if subject.get("ok") else None
    if not box:
        out["crop_reason"] = "no usable subject answer"
        return out
    w, h = (height, width) if deg in (90, 270) else (width, height)
    crop = subject_crop(rotate_box(box, deg), w, h)
    area = (crop[2] - crop[0]) * (crop[3] - crop[1])
    if area > CROP_MAX_AREA:
        out["crop_reason"] = f"subject fills {area:.0%} of the picture; keep it whole"
    elif area < CROP_MIN_AREA:
        out["crop_reason"] = f"subject crop {area:.0%} is too tight; keep it whole"
    elif not deg and display_scale(crop, w, h) < (
            SCROLL_MIN_GAIN if h / w >= panel_framing.TALL_AR else MIN_GAIN) * display_scale(None, w, h):
        out["crop_reason"] = "a subject crop would shrink the art on screen; keep it whole"
    else:
        out["crop_bbox_norm"] = crop
        out["crop_reason"] = (f"auto subject framing ({subject.get('subject') or 'subject'}): "
                              f"{area:.0%} of the picture without balloons/gutters")
    return out


# ------------------------------------------------------------------- vision
def default_ask():
    """Production vision client (describe.ask_image: usage-gated), or None."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or mode() == "off":
        return None
    pd = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "panel-describe")
    if pd not in sys.path:
        sys.path.insert(0, pd)
    import describe as _d
    return lambda jpeg, prompt: _d.ask_image(jpeg, "image/jpeg", prompt, key, MODEL)


def _jpeg(im):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


def subject_image(path):
    im = panel_framing.load_image(path, 0)
    scale = SEND_MAX_EDGE / max(im.size)
    if scale < 1:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))))
    return _jpeg(im)


def turn_sheet(path, order):
    """Three labelled copies side by side, in `order` (clockwise degrees)."""
    b = SHEET_TILE
    sheet = Image.new("RGB", (3 * b + 40, b + 50), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(40)
    except TypeError:                     # Pillow < 10.1
        font = ImageFont.load_default()
    for i, (label, deg) in enumerate(zip("ABC", order)):
        im = panel_framing.load_image(path, deg).convert("RGB")
        s = b / max(im.size)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))))
        x = i * (b + 20)
        sheet.paste(im, (x + (b - im.width) // 2, 50 + (b - im.height) // 2))
        draw.text((x + 8, 4), label, fill="red", font=font)
    return _jpeg(sheet)


def _key(path, kind):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return f"{PROMPT_VERSION}:{kind}:{MODEL}:{h.hexdigest()}"


def _load(cache_path):
    if cache_path and os.path.exists(cache_path):
        try:
            return json.load(open(cache_path))
        except ValueError:
            pass
    return {}


def _save(cache_path, cache):
    if not cache_path:
        return
    tmp = cache_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=1)
    os.replace(tmp, cache_path)


def analyze_panels(panels, ask, cache_path=None, turn_mode="suggest", workers=None):
    """panels: {path: ocr_text}. Returns {path: {"subject": ..., "turn": ...}}.

    Answers are cached by image content + prompt version, so a re-plan never
    pays twice; failures are not cached and are retried next time.
    """
    cache = _load(cache_path)
    jobs = []                                    # (path, kind, key, payload)
    for p, ocr in panels.items():
        try:
            jobs.append((p, "subject", _key(p, "subject"), None))
            g = panel_framing.geometry(p)
            if turn_mode != "off" and g and turn_candidate(g["width"], g["height"], ocr):
                for i, order in enumerate(ORDERS):
                    jobs.append((p, f"turn{i}", _key(p, f"turn{i}"), order))
        except OSError:
            continue
    todo = [j for j in jobs if j[2] not in cache]

    def one(job):
        p, kind, k, order = job
        try:
            if kind == "subject":
                ans = parse_subject(ask(subject_image(p), SUBJECT_PROMPT))
            else:
                t = parse_turn(ask(turn_sheet(p, order), TURN_PROMPT), order)
                ans = {"ok": t is not None, "answer": t}
        except Exception as exc:      # usage cap, network, model error
            ans = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
        return k, ans

    fresh = {}
    if todo and ask:
        from concurrent.futures import ThreadPoolExecutor
        run = one
        try:
            import usage as _usage
            run = _usage.carry(one)
        except Exception:
            pass
        with ThreadPoolExecutor(max_workers=workers or WORKERS) as ex:
            for k, ans in ex.map(run, todo):
                fresh[k] = ans
                if ans.get("ok"):
                    cache[k] = ans
        _save(cache_path, cache)

    out = {}
    for p in panels:
        mine = {kind: cache.get(k) or fresh.get(k) for q, kind, k, _o in jobs if q == p}
        res = {"subject": mine.get("subject") or {"ok": False, "error": "vision unavailable"}}
        turns = [mine[k] for k in sorted(mine) if k.startswith("turn")]
        if turns:
            res["turn"] = combine_turns([(t or {}).get("answer") if (t or {}).get("ok") else None
                                         for t in turns])
        out[p] = res
    return out


# -------------------------------------------------------------- integration
def _ocr_by_panel(desc_path):
    try:
        return {p.get("panel_id"): p.get("ocr_text") or "" for p in json.load(open(desc_path))}
    except (OSError, TypeError, ValueError):
        return {}


def _apply(item, prop, summary):
    """Write one proposal onto a shot or segment dict (same field names)."""
    if prop["rotation_deg"]:
        panel_framing.clear_picture_edits(item)
        item["rotation_deg"] = prop["rotation_deg"]
        item["rotation_source"] = "auto"
        item["rotation_reason"] = prop["rotation_reason"]
        item["crop_bbox_norm"] = list(prop["crop_bbox_norm"] or [0.0, 0.0, 1.0, 1.0])
        item["focus_source"] = "auto_orientation"
        item["focus_reason"] = prop["rotation_reason"]
        summary["turned"] += 1
        return True
    changed = False
    if prop["suggested_turn"] and not item.get("rotation_deg"):
        item["rotation_suggestion"] = prop["suggested_turn"]
        summary["suggested"] += 1
        changed = True
    if item.get("focus_source") in REPLACEABLE_SOURCES and prop["crop_bbox_norm"]:
        item["crop_bbox_norm"] = list(prop["crop_bbox_norm"])
        item["focus_source"] = "auto_subject"
        item["focus_reason"] = prop["crop_reason"]
        item["focus_confidence"] = None
        summary["framed"] += 1
        changed = True
    return changed


def _plan(items, crops_dir, desc_path, ask, cache_path, turn_mode):
    summary = {"panels": 0, "turned": 0, "suggested": 0, "framed": 0, "skipped": None,
               "turn_mode": turn_mode, "changed": []}
    if ask is None:
        summary["skipped"] = "auto framing off or no vision key"
        return summary
    ocr = _ocr_by_panel(desc_path) if desc_path else {}
    by_panel = {}
    for it in items:
        pid = it.get("panel_id") or ""
        if "_shot_" in pid:
            continue                      # sub-shot windows are already framed
        p = it.get("panel_file") if it.get("panel_file") and os.path.exists(it["panel_file"]) \
            else os.path.join(crops_dir, f"{pid}.png")
        if os.path.exists(p):
            by_panel.setdefault(p, []).append(it)
    results = analyze_panels({p: ocr.get(its[0]["panel_id"], "") for p, its in by_panel.items()},
                             ask, cache_path, turn_mode)
    summary["panels"] = len(by_panel)
    for p, its in by_panel.items():
        g = panel_framing.geometry(p)
        if not g:
            continue
        prop = decide(results.get(p), g["width"], g["height"], turn_mode)
        for it in its:
            if _apply(it, prop, summary):
                summary["changed"].append(it.get("seg_index", it.get("index")))
    return summary


def apply_to_shots(shots, crops_dir, desc_path=None, ask=None, cache_path=None):
    """Ingest planner step (after close-up planning, before punch-ins)."""
    ask = ask if ask is not None else default_ask()
    return _plan(shots, crops_dir, desc_path, ask, cache_path, mode())


def apply_to_segments(segs, crops_dir, desc_path=None, ask=None, cache_path=None,
                      turn_mode=None):
    """Board step for an existing chapter. Reviewer decisions are untouched:
    a segment with any rotation or a manual focus source is skipped. Turn
    suggestions cover every other segment (a suggestion changes nothing);
    only default framing is replaced by a subject crop. Mutates `segs` in
    place; returns the summary."""
    ask = ask if ask is not None else default_ask()
    eligible = [s for s in segs
                if not s.get("rotation_deg")
                and not str(s.get("focus_source") or "").startswith("manual")
                and s.get("user_included", True)]
    return _plan(eligible, crops_dir, desc_path, ask, cache_path, turn_mode or mode())
