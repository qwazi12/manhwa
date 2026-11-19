"""Series master thumbnails, style D "Clean+" (owner, 2026-11-07; spec:
Researcher/research/flamingo/thumbnails/THUMBNAIL_STYLE_D_SPEC.md).

If HEAD has put   <thumbnails>/<series_id>/MASTER_<series_id>.png   in Drive
(Researcher/research/flamingo/thumbnails/), the thumbnail uploaded with a
video is that master (1280x720, full art, face centred, no text) with only
the style-D chapter badge stamped top-left. Without a master nothing changes:
the export's own thumbnail is uploaded, exactly as before.

Badge (fixed by the spec): "CH <n>" / "CH <a>-<b>", Anton 78 px white with a
3 px black stroke, rounded box (radius 16, 20 px side padding, height 104),
neon fill, 6 px black outline, black shadow box at (+6, +7), box
origin x=26, y=22.

Badge colour (owner, 2026-11-07: "neon colours so they contrast the
thumbnails"): picked from NEON, the one whose hue is furthest from the
art's colours around the badge and across the image. Since a series reuses one
master, its badge colour stays the same upload to upload. A HEAD override is
still possible: BADGE_<series_id>.json next to the master ({"fill": "#RRGGBB"}).

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
NEON = ("#39FF14", "#00F5FF", "#FF10F0", "#FFF000", "#B026FF", "#FF5F1F", "#1F51FF")
BADGE_AREA = (0, 0, 480, 170)        # where the badge sits, with a margin


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
    """HEAD's override from BADGE_<series_id>.json, or None (= pick a neon)."""
    v = (spec or {}).get("fill")
    if v:
        _colour(v)
    return v or None


def _hue_weights(im):
    """Hue (0-360) of each pixel and its weight = saturation x value, so grey,
    black and white parts of the art count for little."""
    import numpy as np
    hsv = np.asarray(im.convert("HSV")).reshape(-1, 3).astype(float) / 255.0
    return hsv[:, 0] * 360.0, hsv[:, 1] * hsv[:, 2]


def pick_neon(im):
    """The NEON colour whose hue is furthest from the art's colours: around
    the badge (weight 0.65) and across the whole image (0.35)."""
    import colorsys
    import numpy as np
    from PIL import Image
    im = im.convert("RGB")
    sx, sy = im.width / SIZE[0], im.height / SIZE[1]
    a = BADGE_AREA
    parts = ((im.crop((int(a[0] * sx), int(a[1] * sy), int(a[2] * sx), int(a[3] * sy))).resize((96, 34)), 0.65),
             (im.resize((128, 72), Image.BILINEAR), 0.35))
    best, score = NEON[0], -1.0
    for n in NEON:
        r, g, b = _colour(n)
        h = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] * 360
        s_ = 0.0
        for part, w in parts:
            hue, wt = _hue_weights(part)
            d = np.abs(hue - h)
            d = np.minimum(d, 360 - d) / 180
            s_ += w * float((d * wt).sum() / max(wt.sum(), 1e-6))
        if s_ > score:
            best, score = n, s_
    return best


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
    """Master + style-D badge -> a 1280x720 JPEG under YouTube's 2 MB limit.
    fill None = the contrasting neon (pick_neon). Returns (path, fill used)."""
    from PIL import Image
    im = Image.open(master_path).convert("RGB")
    if abs(im.width / im.height - SIZE[0] / SIZE[1]) > 0.02:
        raise ValueError(f"master is {im.width}x{im.height}, not 16:9")
    if im.size != SIZE:
        im = im.resize(SIZE, Image.LANCZOS)
    fill = fill or pick_neon(im)
    _colour(fill)
    draw_badge(im, text, fill)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    for q in (92, 85, 78, 70):
        im.save(out_path, "JPEG", quality=q)
        if os.path.getsize(out_path) <= MAX_BYTES:
            break
    return out_path, fill


# ---- the series' A/B set (owner, 2026-11-19: "Let's use these for the ab thumbnail
# tests ... We need to add the correct chapter numbers ... thumbnails in the studio
# to adhere to ab tests"). Up to three wordless pictures per series, kept on the
# studio's volume. A video of that series carries variant A (or the test's winner,
# once recorded) with its own chapter badge; YouTube's Test & compare gets A, B
# and C with the same badge. It comes before the Drive master.
AB_DIR = "_series_ab"
AB_VARIANTS = ("A", "B", "C")
AB_META = "set.json"


def _ab_dir(root, series_id):
    sid = re.sub(r"[^0-9a-z-]", "", str(series_id or "").lower())
    if not sid:
        raise ValueError("no series id")
    return os.path.join(root, AB_DIR, sid)


def ab_set(root, series_id):
    """-> {"series_id", "variants": {"A": path, ...}, "winner", "options", "updated_at"}
    or None when the series has no set (variant A is required)."""
    try:
        d = _ab_dir(root, series_id)
    except ValueError:
        return None
    files = {v: os.path.join(d, v + ".jpg") for v in AB_VARIANTS}
    files = {v: p for v, p in files.items() if os.path.isfile(p)}
    if "A" not in files:
        return None
    try:
        with open(os.path.join(d, AB_META), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    win = meta.get("winner") if meta.get("winner") in files else None
    return {"series_id": os.path.basename(d), "variants": files, "winner": win,
            "options": meta.get("options") or {}, "source": meta.get("source") or "",
            "updated_at": meta.get("updated_at")}


def _ab_write_meta(d, **changes):
    path = os.path.join(d, AB_META)
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    meta.update(changes)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, path)
    return meta


def ab_save(root, series_id, variant, data, option="", source="", now=None):
    """Store one picture of the set as a 1280x720 JPEG (no badge on it)."""
    import io
    import time
    from PIL import Image
    if variant not in AB_VARIANTS:
        raise ValueError("variant must be A, B or C")
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        raise ValueError("that file is not a picture")
    if abs(im.width / im.height - SIZE[0] / SIZE[1]) > 0.02:
        raise ValueError(f"the picture is {im.width}x{im.height}, not 16:9")
    if im.width < 640:
        raise ValueError(f"the picture is only {im.width} px wide (640 at least)")
    if im.size != SIZE:
        im = im.resize(SIZE, Image.LANCZOS)
    d = _ab_dir(root, series_id)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, variant + ".jpg.tmp")
    im.save(tmp, "JPEG", quality=93)
    os.replace(tmp, os.path.join(d, variant + ".jpg"))
    meta = _ab_write_meta(d)
    opts = dict(meta.get("options") or {})
    opts[variant] = str(option or "")
    _ab_write_meta(d, options=opts, source=str(source or meta.get("source") or ""),
                   updated_at=now if now is not None else time.time())
    return ab_set(root, series_id)


def ab_winner(root, series_id, variant):
    """Record the test's winner ("" clears it): new videos of the series then carry it."""
    st = ab_set(root, series_id)
    if not st:
        raise ValueError("the series has no A/B set")
    if variant and variant not in st["variants"]:
        raise ValueError(f"the set has no variant {variant}")
    _ab_write_meta(_ab_dir(root, series_id), winner=variant or None)
    return ab_set(root, series_id)


def ab_thumbnail(root, series_id, label, out_path, variant=None):
    """The set's picture (variant, else the winner, else A) with this video's
    chapter badge -> (path, variant used, badge colour), or None without a set."""
    st = ab_set(root, series_id)
    if not st or label in (None, ""):
        return None
    v = variant or st["winner"] or "A"
    if v not in st["variants"]:
        raise ValueError(f"the set has no variant {v}")
    out, fill = stamp(st["variants"][v], None, badge_text(label), out_path)
    return out, v, fill


def upload_thumbnail(series_id, label, fallback, work_dir, ds=None, log=None, ab_root=None, ab_series=None):
    """The thumbnail file to upload: the series' A/B set (studio volume) with
    the chapter badge when it has one, else master + badge when HEAD has a
    master for this series in Drive, else `fallback`."""
    say = log or (lambda msg, level="ok": None)
    if not enabled() or not series_id or label in (None, ""):
        return fallback
    if ab_root:
        try:
            got = ab_thumbnail(ab_root, ab_series or series_id, label, os.path.join(
                work_dir, f"series_ab_{re.sub(r'[^0-9A-Za-z-]', '_', str(label))}.jpg"))
            if got:
                say(f"thumbnail = the series' A/B picture {got[1]} + “{badge_text(label)}” badge ({got[2]})", "ok")
                return got[0]
        except Exception as e:      # never block an upload on the set
            say(f"the series' A/B picture was not used — {str(e)[:200]}", "warn")
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
        out, fill = stamp(mp, fill, text, os.path.join(work_dir, f"series_master_{re.sub(r'[^0-9A-Za-z-]', '_', str(label))}.jpg"))
        say(f"thumbnail = series master {series_id} + “{text}” style-D badge ({fill})", "ok")
        return out
    except Exception as e:      # never block an upload on the master
        say(f"series master not used, uploading the export's own thumbnail — {str(e)[:200]}", "warn")
        return fallback
