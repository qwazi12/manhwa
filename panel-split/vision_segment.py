"""
Vision-driven beat segmentation for tall / full-bleed panels.

The geometric splitter (split_panels.py) cuts on white/black gutters. That
fails on webtoon-style tall panels that have NO gutters — continuous art with
caption boxes or speech bubbles overlaid at intervals, where the real "beats"
are defined by MEANING (a caption plus the art it narrates), not by gaps. The
old fallback (a blind content-density sliding window) can't find those beats
and either keeps the whole strip as one unusable image or cuts it arbitrarily.

This module asks the vision model to read the strip the way a person would and
return the ordered beats with their vertical bounds + OCR + description in one
call. It is content-based, so it generalizes across manhwa types (black caption
boxes, white narration boxes, borderless captions, continuous action) instead
of needing a per-style geometric heuristic.

Because the same call returns each beat's ocr_text and visual_description, a
tall panel is split AND described in one step — the sub-crops come out
pre-described, ready for the matcher/narrator with no separate describe pass.

Fail-safe: any error, a missing key, or a single-beat result returns None, and
the caller keeps its existing geometric behavior — never worse than today.
"""

import json
import mimetypes
import os
import sys

# Cost/abuse guardrails (review_ui/usage.py) — optional no-op if unavailable.
_REVIEW_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "manhwa-recap-v1", "review_ui")
if os.path.isdir(_REVIEW_UI) and _REVIEW_UI not in sys.path:
    sys.path.insert(0, _REVIEW_UI)
try:
    import usage
except ImportError:
    usage = None

# Only bother asking the model when a panel is clearly a tall strip; short
# panels are handled fine by the gutter pass and shouldn't cost an API call.
MIN_RATIO_FOR_VISION = 2.2   # height/width above which vision segmentation runs
MIN_BEATS = 2                # fewer than this = treat as one panel (return None)
MIN_BEAT_FRAC = 0.02         # drop degenerate slivers thinner than this frac of H

# Caption-ANCHORED segmentation. Earlier we asked the model for each beat's
# y_start/y_end directly, but those bounds drifted off the actual caption boxes
# — a beat's crop would show a different caption than its stored text (metadata
# desynced from pixels at the segmentation layer). Instead we ask ONLY for each
# caption box's vertical CENTER + its text + a description of the art around it,
# then cut at the midpoints between consecutive caption centers. That guarantees
# each crop contains its own caption, so the stored ocr_text matches the pixels
# by construction. Caption-less action strips yield <2 captions -> fall back to
# the geometric density window.
_PROMPT = """This tall webtoon panel is read top-to-bottom and has several \
CAPTION / NARRATION BOXES (or speech bubbles) at intervals, each narrating the \
art around it.

List every caption/narration box in top-to-bottom order. Return ONLY a JSON \
array; each element:
{"y_center": <0.0-1.0 vertical center of THIS caption box, as a fraction of image height>,
 "text": "<verbatim text inside this caption box>",
 "visual_description": "<one sentence describing the artwork around this caption>"}
One element per caption box, ordered top to bottom. Do not merge boxes."""


def _clean(caps):
    """Turn caption anchors into contiguous beats by cutting at the midpoints
    between consecutive caption centers. Returns [] if unusable."""
    rows = []
    for c in caps:
        try:
            yc = max(0.0, min(1.0, float(c["y_center"])))
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({
            "y_center": yc,
            "ocr_text": (c.get("text") or "").strip(),
            "visual_description": (c.get("visual_description") or "").strip(),
        })
    rows.sort(key=lambda r: r["y_center"])
    if not rows:
        return []
    # cut lines: 0, midpoints between adjacent centers, 1
    cuts = [0.0]
    for a, b in zip(rows, rows[1:]):
        cuts.append((a["y_center"] + b["y_center"]) / 2.0)
    cuts.append(1.0)
    out = []
    for i, r in enumerate(rows):
        y0, y1 = cuts[i], cuts[i + 1]
        if y1 - y0 < MIN_BEAT_FRAC:
            continue
        out.append({
            "y_start": y0, "y_end": y1,
            "ocr_text": r["ocr_text"],
            "visual_description": r["visual_description"],
        })
    return out


def _segment_bytes(img_bytes, mime, api_key, model):
    """Core vision call on raw image bytes → cleaned beats (may be [])."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)

    def _call():
        return client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=img_bytes, mime_type=mime),
                      types.Part(text=_PROMPT)],
            config=types.GenerateContentConfig(
                temperature=0.0, response_mime_type="application/json"),
        )

    if usage:
        with usage.gate("gemini", 1, model=model):
            resp = _call()
    else:
        resp = _call()
    return _clean(json.loads(resp.text))


def segment_tall_panel(image_path, api_key=None, model="gemini-3.5-flash"):
    """Return a list of contiguous beats for a tall panel, or None to fall back.

    Each beat: {y_start, y_end (fractions 0-1), ocr_text, visual_description}.
    None means: not a tall panel, no key, the model failed, or it found a
    single beat — the caller should keep its geometric behavior.
    """
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from PIL import Image
        w, h = Image.open(image_path).size
        if w == 0 or h / w < MIN_RATIO_FOR_VISION:
            return None  # not tall enough to be worth a vision call
        beats = _segment_bytes(open(image_path, "rb").read(),
                               mimetypes.guess_type(image_path)[0] or "image/png",
                               api_key, model)
    except Exception:
        return None
    return beats if len(beats) >= MIN_BEATS else None


def segment_tall_panel_image(pil_img, api_key=None, model="gemini-3.5-flash"):
    """Same as segment_tall_panel but for an in-memory PIL image (a panel crop
    already isolated from a page) — avoids a temp file. Returns beats or None."""
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        w, h = pil_img.size
        if w == 0 or h / w < MIN_RATIO_FOR_VISION:
            return None
        import io
        buf = io.BytesIO()
        pil_img.convert("RGB").save(buf, format="PNG")
        beats = _segment_bytes(buf.getvalue(), "image/png", api_key, model)
    except Exception:
        return None
    return beats if len(beats) >= MIN_BEATS else None


def beats_to_pixel_bboxes(beats, width, height):
    """Convert fractional beats to integer page-pixel bboxes [x0,y0,x1,y1]
    covering the full width, snapped/contiguous over [0, height]."""
    boxes = []
    for b in beats:
        y0 = max(0, int(round(b["y_start"] * height)))
        y1 = min(height, int(round(b["y_end"] * height)))
        if y1 - y0 < 1:
            continue
        boxes.append({
            "bbox": [0, y0, width, y1],
            "ocr_text": b["ocr_text"],
            "visual_description": b["visual_description"],
        })
    return boxes
