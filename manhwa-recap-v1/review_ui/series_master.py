"""Series master thumbnails, style D "Clean+" (owner, 2026-11-07; spec:
Researcher/research/flamingo/thumbnails/THUMBNAIL_STYLE_D_SPEC.md).

If HEAD has put   <thumbnails>/<series_id>/MASTER_<series_id>.png   in Drive
(Researcher/research/flamingo/thumbnails/), the thumbnail uploaded with a
video is that master (1280x720, full art, face centred, no text) with only
the style-D chapter badge stamped top-left. Without a master nothing changes:
the export's own thumbnail is uploaded, exactly as before.

Badge (fixed by the spec): "CH <n>" / "CH <a>-<b>", Anton 78 px white with a
3 px black stroke, rounded box (radius 16, 20 px side padding, height 104),
series-colour fill, 6 px black outline, black shadow box at (+6, +7), box
origin x=26, y=22.

Series colour: BADGE_<series_id>.json next to the master ({"fill": "#RRGGBB"})
or, failing that, SERIES_COLOURS below.

Any problem (Drive down, no colour, an unreadable master) falls back to the
export's own thumbnail and says why; it never blocks an upload.
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

# Style D badge geometry (spec, 1280x720 canvas)
BADGE_X, BADGE_Y = 26, 22
FONT_PX, TEXT_STROKE = 78, 3
PAD_X, BOX_H, RADIUS = 20, 78 + 26, 16
OUTLINE, SHADOW = 6, (6, 7)
# Spec colours so far; other series get theirs from BADGE_<series_id>.json.
SERIES_COLOURS = {
    "revenge-of-the-iron-blooded-sword-hound": "#D61E2D",      # Baskerville
    "return-of-the-apocalypse-class-death-knight": "#28AADC",
    "the-stellar-swordmaster": "#1E6EE6",
}


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
    """-> (master file, badge file or None) from Drive, or None when the series has no master."""
    folder = _child(ds, FOLDER_ID, series_id, folder=True)
    if not folder:
        return None
    master = _child(ds, folder["id"], f"MASTER_{series_id}.png")
    if not master:
        return None
    return master, _child(ds, folder["id"], f"BADGE_{series_id}.json")


def badge_text(label):
    return f"CH {label}".strip()


def _colour(v):
    v = (v or "").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
        raise ValueError(f"badge colour {v!r} is not #RRGGBB")
    return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))


def series_colour(series_id, spec=None):
    v = (spec or {}).get("fill") or SERIES_COLOURS.get(series_id)
    if not v:
        raise ValueError(f"no badge colour for {series_id}: add BADGE_{series_id}.json "
                         '({"fill": "#RRGGBB"}) next to the master')
    _colour(v)
    return v


def _font_path():
    p = os.path.join(FONTS, "Anton-Regular.ttf")
    if not os.path.isfile(p):
        raise ValueError("Anton-Regular.ttf is missing from review_ui/fonts/")
    return p


def finish_art(im):
    """The spec's light finish for a new master: contrast and saturation x1.08,
    unsharp mask (radius 2, 60 %, threshold 2). Applied once, when the master is made."""
    from PIL import ImageEnhance, ImageFilter
    im = ImageEnhance.Contrast(im.convert("RGB")).enhance(1.08)
    im = ImageEnhance.Color(im).enhance(1.08)
    return im.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))


def draw_badge(im, text, fill):
    """Style-D badge on a 1280x720 RGB image, in place. Returns the box (x0, y0, x1, y1)."""
    from PIL import ImageDraw, ImageFont
    font = ImageFont.truetype(_font_path(), FONT_PX)
    d = ImageDraw.Draw(im)
    l, t, r, b = d.textbbox((0, 0), text, font=font, stroke_width=TEXT_STROKE)
    x0, y0 = BADGE_X, BADGE_Y
    x1, y1 = x0 + (r - l) + 2 * PAD_X, y0 + BOX_H
    d.rounded_rectangle((x0 + SHADOW[0], y0 + SHADOW[1], x1 + SHADOW[0], y1 + SHADOW[1]),
                        radius=RADIUS, fill=(0, 0, 0))
    d.rounded_rectangle((x0, y0, x1, y1), radius=RADIUS, fill=_colour(fill),
                        outline=(0, 0, 0), width=OUTLINE)
    d.text((x0 + PAD_X - l, y0 + (BOX_H - (b - t)) / 2 - t), text, font=font,
           fill=(255, 255, 255), stroke_width=TEXT_STROKE, stroke_fill=(0, 0, 0))
    return x0, y0, x1, y1


def stamp(master_path, fill, text, out_path):
    """Master + style-D badge -> a 1280x720 JPEG under YouTube's 2 MB limit."""
    from PIL import Image
    _colour(fill)
    im = Image.open(master_path).convert("RGB")
    if abs(im.width / im.height - SIZE[0] / SIZE[1]) > 0.02:
        raise ValueError(f"master is {im.width}x{im.height}, not 16:9")
    if im.size != SIZE:
        im = im.resize(SIZE, Image.LANCZOS)
    draw_badge(im, text, fill)
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
        spec = {}
        if badge:
            bp = os.path.join(work_dir, f"BADGE_{series_id}.json")
            ds.download(badge["id"], bp)
            spec = json.load(open(bp, encoding="utf-8"))
        fill = series_colour(series_id, spec)
        text = badge_text(label)
        out = stamp(mp, fill, text, os.path.join(work_dir, f"series_master_{re.sub(r'[^0-9A-Za-z-]', '_', str(label))}.jpg"))
        say(f"thumbnail = series master {series_id} + “{text}” style-D badge ({fill})", "ok")
        return out
    except Exception as e:      # never block an upload on the master
        say(f"series master not used, uploading the export's own thumbnail — {str(e)[:200]}", "warn")
        return fallback
