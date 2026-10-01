"""Custom thumbnails for a rendered export.

Stores, validates and serves a thumbnail for an export. At publish time the
saved file (exports/_thumbs/) is sent to Upload-Post as the multipart
"thumbnail" field (documented: JPG/PNG, max 2 MB; YouTube only applies custom
thumbnails on verified channels). `publish_note()` is the single place that
tells the operator what happens to it.

Limits are YouTube's own, so a thumbnail accepted here is one YouTube accepts.
"""
import json
import os
import time

# YouTube's published limits (Upload-Post documents the same 2 MB cap).
MAX_BYTES = 2 * 1024 * 1024
ALLOWED = ("JPEG", "PNG")
MIN_WIDTH = 640                     # YouTube rejects narrower
IDEAL = (1280, 720)

THUMB_DIR = "_thumbs"
INDEX = "index.json"


class ThumbnailError(Exception):
    """Rejected input. The message is shown to the operator verbatim, so it
    has to say what is wrong AND what would be acceptable."""


# ------------------------------------------------------------------ paths
def _dir(pdir):
    return os.path.join(pdir, "exports", THUMB_DIR)


def _index_path(pdir):
    return os.path.join(_dir(pdir), INDEX)


def _load_index(pdir):
    try:
        with open(_index_path(pdir), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_index(pdir, data):
    os.makedirs(_dir(pdir), exist_ok=True)
    tmp = _index_path(pdir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, _index_path(pdir))


# ------------------------------------------------------------- validation
def inspect(data):
    """Sniff the bytes. Returns (format, width, height).

    The browser's reported MIME type is not trusted — it is attacker- and
    accident-controlled, and a renamed .webp would sail through a name check
    and then be rejected by YouTube much later, after a publish.
    """
    if not data:
        raise ThumbnailError("that file was empty")
    if len(data) > MAX_BYTES:
        raise ThumbnailError(
            "that image is %.1f MB. YouTube's limit is 2 MB — re-export it "
            "smaller or save as JPEG." % (len(data) / 1024 / 1024))
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            fmt, (w, h) = im.format, im.size
            im.verify()
    except ThumbnailError:
        raise
    except Exception:
        raise ThumbnailError(
            "that file is not a readable image. YouTube accepts JPEG or PNG.")
    if fmt not in ALLOWED:
        raise ThumbnailError(
            "that image is %s. YouTube accepts JPEG or PNG only." % fmt)
    if w < MIN_WIDTH:
        raise ThumbnailError(
            "that image is %d px wide. YouTube needs at least %d px "
            "(1280x720 is ideal)." % (w, MIN_WIDTH))
    return fmt, w, h


def advisories(w, h):
    """Non-blocking notes. These are NOT errors: YouTube accepts the image,
    it just will not look its best, and that is the operator's call."""
    out = []
    if (w, h) != IDEAL:
        out.append("%dx%d — 1280x720 is YouTube's recommended size." % (w, h))
    if h and abs((w / h) - (16 / 9)) > 0.02:
        out.append("Not 16:9, so YouTube will letterbox or crop it.")
    return out


# ---------------------------------------------------------------- storage
def _safe(name):
    return os.path.basename(name or "").replace("/", "")


def save(pdir, export_name, data):
    """Validate and store one thumbnail for one export. Replaces any previous.

    Written to a temp file and renamed so a failed or half-finished upload can
    never leave a truncated image where a valid one used to be.
    """
    name = _safe(export_name)
    if not name:
        raise ThumbnailError("no export was named")
    fmt, w, h = inspect(data)
    ext = "jpg" if fmt == "JPEG" else "png"
    os.makedirs(_dir(pdir), exist_ok=True)
    fname = name + "." + ext
    path = os.path.join(_dir(pdir), fname)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

    idx = _load_index(pdir)
    prev = idx.get(name) or {}
    # A replacement in a different format would otherwise strand the old file.
    if prev.get("file") and prev["file"] != fname:
        try:
            os.remove(os.path.join(_dir(pdir), prev["file"]))
        except OSError:
            pass
    rec = {"file": fname, "format": fmt, "width": w, "height": h,
           "bytes": len(data), "uploaded_at": time.time(),
           "advisories": advisories(w, h)}
    idx[name] = rec
    _save_index(pdir, idx)
    return rec


def get(pdir, export_name):
    """The stored record, or {} — and it self-heals: an index entry whose file
    has been deleted underneath us is dropped rather than reported as present,
    which would render a broken preview."""
    name = _safe(export_name)
    idx = _load_index(pdir)
    rec = idx.get(name)
    if not rec:
        return {}
    if not os.path.exists(os.path.join(_dir(pdir), rec.get("file", ""))):
        idx.pop(name, None)
        _save_index(pdir, idx)
        return {}
    return rec


def path_for(pdir, export_name):
    rec = get(pdir, export_name)
    return os.path.join(_dir(pdir), rec["file"]) if rec else ""


def delete(pdir, export_name):
    name = _safe(export_name)
    idx = _load_index(pdir)
    rec = idx.pop(name, None)
    if not rec:
        return False
    try:
        os.remove(os.path.join(_dir(pdir), rec["file"]))
    except OSError:
        pass
    _save_index(pdir, idx)
    return True


# ------------------------------------------------------------------ truth
def publish_note(has_thumb):
    """What the operator is told about this thumbnail at publish time."""
    if not has_thumb:
        return {"sent_with_post": False,
                "detail": "No custom thumbnail. YouTube will auto-pick a frame."}
    return {
        "sent_with_post": True,
        "detail": ("Sent with the post via Upload-Post. YouTube applies custom "
                   "thumbnails only on verified channels — check it in YouTube "
                   "Studio once the video is up."),
    }