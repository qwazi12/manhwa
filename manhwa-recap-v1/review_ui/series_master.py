"""Series master thumbnails (owner, 2026-11-07: HEAD owns them).

If HEAD has put   <thumbnails>/<series_id>/MASTER_<series_id>.png   in Drive
(Researcher/research/flamingo/thumbnails/), the thumbnail uploaded with a
video is that master with the chapter badge stamped on it, as described by
BADGE_<series_id>.json next to it. Without a master nothing changes: the
export's own thumbnail is uploaded, exactly as before.

BADGE_<series_id>.json (all but "box" optional):
  {"box": [x, y, w, h],        pixels on the master image
   "fill": "#D62F27",          badge colour
   "text": "#FFFFFF",          text colour
   "stroke": "#000000",        text outline ("" for none)
   "font": "Anton",            a font in review_ui/fonts/
   "radius": 14,               corner radius in pixels
   "prefix": "CH "}            text before the chapter number

Any problem (Drive down, a bad badge file, an unreadable master) falls back to
the export's own thumbnail and says why; it never blocks an upload.
Off switch (rollback without a deploy of new code): SERIES_MASTER_OFF=1.
"""
import hashlib
import json
import os
import re

# Researcher/research/flamingo/thumbnails (Flamingo Recap shared drive)
FOLDER_ID = os.environ.get("SERIES_MASTER_FOLDER_ID") or "1zEsuNiXXwQx11aDv13qiZKvXP6K0oyjH"
FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
SIZE = (1280, 720)
MAX_BYTES = 2 * 1024 * 1024          # YouTube's custom-thumbnail limit
FOLDER_MIME = "application/vnd.google-apps.folder"


def enabled():
    return os.environ.get("SERIES_MASTER_OFF", "").strip().lower() not in ("1", "true", "yes", "on")


def _q(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _child(ds, parent, name, folder=False):
    q = f"'{parent}' in parents and name = '{_q(name)}' and trashed = false"
    if folder:
        q += f" and mimeType = '{FOLDER_MIME}'"
    r = ds._request("GET", "https://www.googleapis.com/drive/v3/files", params={
        "q": q, "fields": "files(id,name,md5Checksum,modifiedTime)", "pageSize": 10,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "corpora": "allDrives"})
    files = r.get("files") or []
    return files[0] if files else None


def lookup(series_id, ds):
    """-> (master file, badge file) from Drive, or None when the series has no master."""
    folder = _child(ds, FOLDER_ID, series_id, folder=True)
    if not folder:
        return None
    master = _child(ds, folder["id"], f"MASTER_{series_id}.png")
    if not master:
        return None
    badge = _child(ds, folder["id"], f"BADGE_{series_id}.json")
    if not badge:
        raise ValueError(f"MASTER_{series_id}.png has no BADGE_{series_id}.json next to it")
    return master, badge


def badge_text(label, spec):
    return f"{spec.get('prefix', 'CH ')}{label}".strip()


def _colour(v, default):
    v = (v or default).strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
        raise ValueError(f"badge colour {v!r} is not #RRGGBB")
    return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))


def _font_path(name):
    want = re.sub(r"[^a-z0-9]", "", (name or "Anton").lower())
    for f in sorted(os.listdir(FONTS)):
        if f.lower().endswith((".ttf", ".otf")) and re.sub(r"[^a-z0-9]", "", f.lower()).startswith(want):
            return os.path.join(FONTS, f)
    raise ValueError(f"badge font {name!r} is not in review_ui/fonts/")


def check_spec(spec):
    box = spec.get("box")
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(x, (int, float)) for x in box)
            and box[2] > 0 and box[3] > 0):
        raise ValueError("badge box must be [x, y, w, h] in pixels")
    _colour(spec.get("fill"), "#D62F27")
    _colour(spec.get("text"), "#FFFFFF")
    if spec.get("stroke", "#000000"):
        _colour(spec.get("stroke"), "#000000")
    _font_path(spec.get("font"))
    return spec


def stamp(master_path, spec, text, out_path):
    """Master + badge -> a 1280x720 JPEG under YouTube's 2 MB limit."""
    from PIL import Image, ImageDraw, ImageFont
    check_spec(spec)
    im = Image.open(master_path).convert("RGB")
    sx, sy = SIZE[0] / im.width, SIZE[1] / im.height
    im = im.resize(SIZE, Image.LANCZOS)
    x, y, w, h = spec["box"]
    x, y, w, h = x * sx, y * sy, w * sx, h * sy
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((x, y, x + w, y + h), radius=int(spec.get("radius", 14) * min(sx, sy)),
                        fill=_colour(spec.get("fill"), "#D62F27"))
    stroke = spec.get("stroke", "#000000")
    sw = max(2, int(h * 0.05)) if stroke else 0
    fp = _font_path(spec.get("font"))
    size = int(h * 0.75)
    while size > 8:
        font = ImageFont.truetype(fp, size)
        b = d.textbbox((0, 0), text, font=font, stroke_width=sw)
        if b[2] - b[0] <= w * 0.88 and b[3] - b[1] <= h * 0.72:
            break
        size -= 2
    b = d.textbbox((0, 0), text, font=font, stroke_width=sw)
    tx = x + (w - (b[2] - b[0])) / 2 - b[0]
    ty = y + (h - (b[3] - b[1])) / 2 - b[1]
    d.text((tx, ty), text, font=font, fill=_colour(spec.get("text"), "#FFFFFF"),
           stroke_width=sw, stroke_fill=_colour(stroke, "#000000") if stroke else None)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    for q in (92, 85, 78, 70):
        im.save(out_path, "JPEG", quality=q)
        if os.path.getsize(out_path) <= MAX_BYTES:
            break
    return out_path


def upload_thumbnail(series_id, label, fallback, work_dir, ds=None, log=None):
    """The thumbnail file to upload: master + badge when HEAD has a master
    for this series, else `fallback` (today's behaviour)."""
    say = log or (lambda msg, level="ok": None)
    if not enabled() or not series_id or label in (None, ""):
        return fallback
    try:
        if ds is None:
            import drive_store as ds
            if not ds.configured():
                return fallback
        found = lookup(series_id, ds)
        if not found:
            return fallback
        master, badge = found
        os.makedirs(work_dir, exist_ok=True)
        mp = os.path.join(work_dir, f"MASTER_{series_id}.{master.get('md5Checksum') or 'x'}.png")
        if not os.path.isfile(mp):
            ds.download(master["id"], mp)
        if master.get("md5Checksum") and hashlib.md5(open(mp, "rb").read()).hexdigest() != master["md5Checksum"]:
            os.remove(mp)
            raise ValueError("the master downloaded incompletely")
        bp = os.path.join(work_dir, f"BADGE_{series_id}.json")
        ds.download(badge["id"], bp)
        spec = json.load(open(bp, encoding="utf-8"))
        text = badge_text(label, spec)
        out = stamp(mp, spec, text, os.path.join(work_dir, f"series_master_{re.sub(r'[^0-9A-Za-z-]', '_', str(label))}.jpg"))
        say(f"thumbnail = series master {series_id} + “{text}” badge", "ok")
        return out
    except Exception as e:      # never block an upload on the master
        say(f"series master not used, uploading the export's own thumbnail — {str(e)[:200]}", "warn")
        return fallback
