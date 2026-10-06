"""Face boxes for thumbnail panel selection (spec 07 §5 STEP 2).

Owner decision 2026-10-06 (after measuring ch.358's 51 panels: YOLO person
boxes 13/51 and missing most close-ups, OpenCV's anime cascade 4/51, Haar 27/51
but mostly on speech bubbles): faces come from ONE Gemini vision pass per
chapter — ANALYSIS only, nothing is drawn — metered by usage.gate like every
other model call. Measured cost: ~0.9 cents per 10 panels (512px, medium media
resolution); ch.358's first run at 640px/default cost ~14 cents for 51 panels.

For each panel image the model returns, in coordinates normalised to that image:
  faces[]         {box [x0,y0,x1,y1], name, is_mc}  — is_mc judged against the
                  series cast list (Series Bible names + visual cues)
  bubbles[]       speech-bubble / caption boxes (for bubble suppression)
  text_blocks[]   title logo / credits / site lettering (cover typography trim)
  bubble_coverage fraction of the panel covered by bubbles/captions
  watermark       a site watermark or credits text is visible
Cached in <project>/face_boxes.json keyed by panel id + file size/mtime, so a
chapter is analysed once.
"""
import base64
import io
import json
import os
import re
import sys

_RECAP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RECAP not in sys.path:
    sys.path.insert(0, _RECAP)

CACHE = "face_boxes.json"
BATCH = 10
MAX_SIDE = 512


def _key(path):
    try:
        st = os.stat(path)
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def load(pdir):
    try:
        with open(os.path.join(pdir, CACHE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(pdir, data):
    tmp = os.path.join(pdir, CACHE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, os.path.join(pdir, CACHE))


def _jpeg_b64(path):
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def cast_brief(bible):
    out = []
    for c in (bible or {}).get("characters") or []:
        if not c.get("name"):
            continue
        cues = c.get("visual_cues") or ""
        if isinstance(cues, list):
            cues = "; ".join(str(x) for x in cues)
        out.append(f"- {c['name']} ({str(c.get('role') or '')[:80]}): {str(cues)[:220]}")
    return "\n".join(out[:10]) or "(no cast list)"


def prompt(n, cast, mc):
    return f"""You are given {n} manhwa panel images, numbered 0..{n - 1} in order.
For EACH image return where the faces are, so a thumbnail can be cropped around the main character.

Series cast (names and how they look):
{cast}
Main character: {mc or "the protagonist in the cast list"}

Return ONLY a JSON array with one object per image, in order:
{{"i": <image number>,
  "faces": [{{"box": [x0, y0, x1, y1], "name": "<cast name or ''>", "is_mc": true|false}}],
  "bubbles": [[x0, y0, x1, y1]],
  "text_blocks": [[x0, y0, x1, y1]],
  "bubble_coverage": <0..1, fraction of the image covered by speech bubbles, captions or sound-effect text boxes>,
  "watermark": true|false}}
Rules: coordinates are fractions of the image width/height (0..1), x0<x1, y0<y1.
text_blocks: title logos, series-name lettering, credits or site text that is NOT a speech bubble.
A face box covers the face only (forehead to chin, ear to ear), not hair or body.
Include every clearly visible face, largest first. is_mc only when the face matches the main character's look.
No face visible -> "faces": []."""


def _parse(text):
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    i, j = t.find("["), t.rfind("]")
    return json.loads(t[i:j + 1]) if i >= 0 and j > i else []


def _clean_box(b):
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in b[:4])
    except Exception:
        return None
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def detect(pdir, panels, bible=None, api_key=None, _post=None, batch=BATCH):
    """panels: [(panel_id, file_path)]. Returns {panel_id: record}; analyses
    only panels not already cached. Raises usage.UsageCapExceeded from the gate."""
    import series_research as sr
    data = load(pdir)
    todo = [(pid, f) for pid, f in panels if (data.get(pid) or {}).get("key") != _key(f)]
    leads = [c for c in (bible or {}).get("characters") or []
             if "protagonist" in str(c.get("role") or "").lower()]
    mc = (leads or (bible or {}).get("characters") or [{}])[0].get("name") if (bible or {}).get("characters") else ""
    cast = cast_brief(bible)
    post = _post or (lambda body: sr._post(body, api_key))
    for k in range(0, len(todo), batch):
        chunk = todo[k:k + batch]
        parts = [{"text": prompt(len(chunk), cast, mc)}]
        for n, (pid, f) in enumerate(chunk):
            parts += [{"text": f"Image {n}:"}, {"inline_data": {"mime_type": "image/jpeg", "data": _jpeg_b64(f)}}]
        resp = post({"contents": [{"parts": parts}],
                     "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192,
                                          "responseMimeType": "application/json",
                                          # measured on ch.358: same boxes as 640px/default
                                          # resolution at about half the prompt tokens
                                          "mediaResolution": "MEDIA_RESOLUTION_MEDIUM"}})
        try:
            rows = _parse(sr._text(resp))
        except ValueError:
            rows = []
        by = {int(r.get("i", -1)): r for r in rows if isinstance(r, dict)}
        for n, (pid, f) in enumerate(chunk):
            r = by.get(n) or {}
            faces = []
            for fc in r.get("faces") or []:
                b = _clean_box((fc or {}).get("box") or [])
                if b:
                    faces.append({"box": b, "name": str(fc.get("name") or "")[:60], "is_mc": bool(fc.get("is_mc"))})
            faces.sort(key=lambda x: -((x["box"][2] - x["box"][0]) * (x["box"][3] - x["box"][1])))
            data[pid] = {"key": _key(f), "faces": faces,
                         "bubbles": [b for b in (_clean_box(x) for x in r.get("bubbles") or []) if b],
                         "text_blocks": [b for b in (_clean_box(x) for x in r.get("text_blocks") or []) if b],
                         "bubble_coverage": max(0.0, min(1.0, float(r.get("bubble_coverage") or 0))),
                         "watermark": bool(r.get("watermark")), "analysed": bool(r)}
        _save(pdir, data)
    return {pid: data.get(pid) for pid, _f in panels}
