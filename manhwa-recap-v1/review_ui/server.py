"""
Review UI backend (MVP-1: read-only review + approve).

A thin FastAPI layer over the existing segment pipeline. It does NOT reimplement
any logic — it serves the `segments.json` manifest, the per-segment rendered
clips, and panel thumbnails, tracks an approve/reject decision per segment in a
side file (review.json, never mutating segments.json), and exports a final MP4
by concatenating ONLY the approved clips (optionally through the 1.5x pass).

Run:
    cd manhwa-recap-v1
    ./venv/bin/python -m uvicorn review_ui.server:app --reload --port 8000
    # open http://localhost:8000

Everything is deploy-agnostic: put this behind any reverse proxy / subdomain
(e.g. manhwa.kymediamgmt.com) later without code changes.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # so same-dir modules (usage, ingest) resolve
                               # regardless of launch cwd/app-dir
import usage  # cost/abuse guardrails — same dir (path set just above)

RECAP = os.path.abspath(os.path.join(HERE, ".."))
HF = os.path.join(RECAP, "hyperframes")
WORK = os.path.join(HF, "segments-workspace")
CLIPS = os.path.join(WORK, "clips")
SEGMENTS_JSON = os.path.join(WORK, "segments.json")
REVIEW_JSON = os.path.join(HERE, "review.json")
THUMBS = os.path.join(HERE, "thumbnails")
EXPORTS = os.path.join(WORK, "exports")

os.makedirs(THUMBS, exist_ok=True)
os.makedirs(EXPORTS, exist_ok=True)

app = FastAPI(title="Manhwa Recap — Review UI")


# Fail-fast check for SHARED_SECRET in production environment
if os.environ.get("RAILWAY_ENVIRONMENT") and not os.environ.get("SHARED_SECRET"):
    raise RuntimeError("Missing SHARED_SECRET environment variable in production")


@app.middleware("http")
async def verify_shared_secret(request: Request, call_next):
    path = request.url.path
    protected_prefixes = ("/api", "/clip", "/thumb", "/audio", "/panelimg", "/export")
    if any(path.startswith(prefix) for prefix in protected_prefixes):
        expected_secret = os.environ.get("SHARED_SECRET")
        if expected_secret:
            auth_header = request.headers.get("x-shared-secret")
            if auth_header != expected_secret:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized: Invalid or missing shared secret"}
                )
    return await call_next(request)


# Measured: the board's HTML is ~430 KB and /api/project ~87 KB, both shipped
# uncompressed — they gzip to ~57 KB and ~13 KB (87% / 85% smaller).
#
# Starlette's own GZipMiddleware is deliberately NOT used: it does not look at
# content-type, so it would also gzip the MP4 clips and PNG panels this server
# streams. That burns CPU on a CPU-limited container for ~0 bytes saved, and
# clips are fetched constantly during review. This compresses text only, and
# returns file/stream responses untouched so large media is never buffered.
_COMPRESSIBLE = ("text/", "application/json", "application/javascript",
                 "image/svg+xml")
_GZIP_MIN = 1024


@app.middleware("http")
async def gzip_text(request: Request, call_next):
    response = await call_next(request)
    if "gzip" not in request.headers.get("accept-encoding", "").lower():
        return response
    ctype = (response.headers.get("content-type") or "").lower()
    if not any(ctype.startswith(c) or c in ctype for c in _COMPRESSIBLE):
        return response
    if response.headers.get("content-encoding"):
        return response
    body = b""
    async for chunk in response.body_iterator:
        body += chunk if isinstance(chunk, bytes) else chunk.encode()
    if len(body) < _GZIP_MIN:
        # Below this, the gzip header costs more than it saves.
        return Response(content=body, status_code=response.status_code,
                        headers=dict(response.headers), media_type=ctype or None)
    import gzip as _gz
    packed = _gz.compress(body, 6)
    headers = dict(response.headers)
    headers["content-encoding"] = "gzip"
    headers["content-length"] = str(len(packed))
    headers["vary"] = "Accept-Encoding"
    return Response(content=packed, status_code=response.status_code,
                    headers=headers, media_type=ctype or None)


# ----------------------------------------------------------------- project-scoped workspace
# The active-project pointer is read on almost every code path, and
# /api/project used to re-read it once PER SEGMENT — 206 opens of the same
# ~40-byte file in a single request on a 103-segment project (measured).
# Cache it against the file's mtime+size: a stat is far cheaper than an
# open+read, and an ingest switching the active project still invalidates
# immediately because writing the file changes both.
_ACTIVE_CACHE = {"key": None, "value": ""}


def get_active_project_id():
    path = os.path.join(WORK, "active_project.txt")
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        _ACTIVE_CACHE["key"] = None
        return ""
    if _ACTIVE_CACHE["key"] == key:
        return _ACTIVE_CACHE["value"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            val = f.read().strip()
    except Exception:
        return ""
    _ACTIVE_CACHE["key"] = key
    _ACTIVE_CACHE["value"] = val
    return val


def active_project_dir():
    pid = get_active_project_id()
    if pid:
        import ingest
        return os.path.join(ingest.PROJECTS, pid)
    return WORK


def active_exports_dir():
    path = os.path.join(active_project_dir(), "exports")
    os.makedirs(path, exist_ok=True)
    return path


# Exports are kept this long, then removed to reclaim disk. Nothing used to
# delete them at all — they only LOOKED like they vanished because the drawer
# listed the ACTIVE project's folder only, so switching project (an ingest
# does that automatically) hid every earlier export.
EXPORT_RETENTION_DAYS = int(os.environ.get("EXPORT_RETENTION_DAYS", "7"))


def _all_export_dirs():
    """(project_id, exports_dir) for every project that has one."""
    import ingest as _ing
    out = []
    try:
        for pid in sorted(os.listdir(_ing.PROJECTS)):
            if pid.startswith("_"):
                continue
            d = os.path.join(_ing.PROJECTS, pid, "exports")
            if os.path.isdir(d):
                out.append((pid, d))
    except OSError:
        pass
    return out


def prune_exports():
    """Delete exports past the retention window. Returns what it removed."""
    cutoff = time.time() - EXPORT_RETENTION_DAYS * 86400
    removed = []
    # Publishing Studio: a video waiting in the posting queue is kept until it
    # is posted or taken off the queue.
    try:
        import ingest as _ing
        import publish_queue as _pq
        keep = _pq.protected(_ing.PROJECTS)
    except Exception:
        keep = set()
    for pid, d in _all_export_dirs():
        for f in os.listdir(d):
            if not f.endswith(".mp4") or (pid, f) in keep:
                continue
            fp = os.path.join(d, f)
            try:
                if os.stat(fp).st_mtime < cutoff:
                    os.remove(fp)
                    removed.append({"project": pid, "name": f})
            except OSError:
                pass
    return removed


# ----------------------------------------------------------------- state
def _media_pdir(project=""):
    """The chapter a media URL belongs to. Owner, 2026-10-06: the Chapter page
    showed ch.31's pictures on ch.32 while ch.31 rendered, because every media
    route read the ACTIVE chapter and panel ids repeat across chapters. A page
    now names its chapter (?project=); without it, the active one as before."""
    return project_dir_for(project) if project else active_project_dir()


def _segments_of(pdir):
    try:
        with open(os.path.join(pdir, "segments.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def load_segments():
    path = os.path.join(active_project_dir(), "segments.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_segments(segs):
    path = os.path.join(active_project_dir(), "segments.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(segs, f, indent=2)
    os.replace(tmp, path)


def load_review():
    path = os.path.join(active_project_dir(), "review.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_review(state):
    path = os.path.join(active_project_dir(), "review.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


# ------------------------------------------------------------- thumbnails
def _crop_api():
    """shot_planner's crop contract — the SAME functions the exporter uses."""
    if RECAP not in sys.path:
        sys.path.insert(0, RECAP)
    import shot_planner
    return shot_planner


def _panel_px_size(path):
    """True (w, h) of the panel PNG — the crop rect must be in real pixels,
    not in whatever width/height segments.json happens to carry."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            import struct
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return None, None


def seg_crop_rect(seg):
    """(x, y, w, h) this segment is cropped to for the video, or None.

    P2 (Session 25): the board used to show the raw panel while the exporter
    applied crop_bbox_norm, so an approved frame was not the delivered frame.
    Both sides now resolve the box through shot_planner, so preview == export.
    """
    sp = _crop_api()
    src = seg.get("panel_file")
    if not src or not os.path.exists(src):
        return None
    if not sp.is_sub_crop(seg.get("crop_bbox_norm")):
        return None                       # full-frame box == no crop (P4)
    w, h = _panel_px_size(src)
    if not (w and h):                     # unreadable header -> trust the manifest
        w, h = seg.get("width"), seg.get("height")
    return sp.crop_rect_px(seg.get("crop_bbox_norm"), w, h)


def _thumb_key(seg):
    """Cache key: changes whenever the RENDERED framing changes, so a panel
    swap or a re-planned crop can never keep serving the old thumbnail."""
    import hashlib
    rect = seg_crop_rect(seg)
    sig = f"{seg.get('panel_id')}|{rect or 'full'}"
    return hashlib.md5(sig.encode()).hexdigest()[:8]


_THUMBDIR_MADE = set()


def thumb_path(seg_index, seg=None, pdir=None):
    t_dir = os.path.join(pdir or active_project_dir(), "thumbnails")
    # os.makedirs ran once per segment on every /api/project. The directory
    # only has to be created once per process per project.
    if t_dir not in _THUMBDIR_MADE:
        os.makedirs(t_dir, exist_ok=True)
        _THUMBDIR_MADE.add(t_dir)
    if seg is None:
        return os.path.join(t_dir, f"seg_{seg_index:03d}.jpg")
    return os.path.join(t_dir, f"seg_{seg_index:03d}_{_thumb_key(seg)}.jpg")


def ensure_thumb(seg, pdir=None):
    """Small JPEG of the frame the VIDEO will show for this segment (cached).

    Cropped to crop_bbox_norm when the segment actually sub-crops; the whole
    panel otherwise. Any failure falls back to the uncropped panel rather than
    showing nothing.
    """
    out = thumb_path(seg["seg_index"], seg, pdir)
    if os.path.exists(out):
        return out
    src = seg.get("panel_file")
    if not src or not os.path.exists(src):
        return None
    rect = seg_crop_rect(seg)
    vf = "scale=200:-1"
    if rect:
        x, y, w, h = rect
        vf = f"crop={w}:{h}:{x}:{y},scale=200:-1"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", src, "-vf", vf,
                        "-frames:v", "1", out], check=True, capture_output=True)
        return out
    except Exception:
        if not rect:
            return None
        try:                              # crop failed -> never show nothing
            subprocess.run(["ffmpeg", "-y", "-i", src, "-vf", "scale=200:-1",
                            "-frames:v", "1", out], check=True, capture_output=True)
            return out
        except Exception:
            return None


# ---------------------------------------------------------------- routes
@app.get("/api/project")
def project():
    segs = load_segments()
    review = load_review()
    total = segs[-1]["end"] if segs else 0
    out = []
    approved_dur = 0.0
    pdir = active_project_dir()          # once, not once per segment
    for s in segs:
        st = review.get(str(s["seg_index"]), {}).get("status", "pending")
        note = review.get(str(s["seg_index"]), {}).get("note", "")
        clip_ok = os.path.exists(os.path.join(pdir, s.get("clip", "")))
        if st == "approved":
            approved_dur += s.get("dur", 0)
        # ensure_thumb() used to run HERE, once per segment. On a cold cache
        # that is an ffmpeg spawn per segment (~40ms each, 103 segments = ~4s
        # measured) serialized inside a single GET, and the cache key includes
        # the crop, so ANY re-crop re-paid it. /thumb/{seg_index} already
        # builds the thumbnail on demand, the <img> tags are loading="lazy",
        # and the browser fetches several at once — so the work now happens
        # per-image, only for thumbnails actually scrolled into view, instead
        # of all of them up front while the page waits.
        out.append({
            "seg_index": s["seg_index"],
            "panel_id": s["panel_id"],
            "user_included": bool(s.get("user_included")),
            "clip_exists": clip_ok,
            "start": s["start"], "end": s["end"], "dur": s.get("dur"),
            "text": " ".join(b["text"] for b in s.get("beats", [])),
            "n_beats": len(s.get("beats", [])),
            "beats": [{"index": b["index"], "text": b["text"],
                       "start": b.get("start"), "end": b.get("end"),
                       "dur": round(b.get("end", 0) - b.get("start", 0), 3)}
                      for b in s.get("beats", [])],
            "status": st, "note": note,
            "has_clip": clip_ok,
            "clip_url": f"/clip/{s['seg_index']}" if clip_ok else None,
            "thumb_url": f"/thumb/{s['seg_index']}",
            "crop_bbox_norm": s.get("crop_bbox_norm"),
            "focus_source": s.get("focus_source"),
            "focus_reason": s.get("focus_reason"),
            "focus_confidence": s.get("focus_confidence"),
            "width": s.get("width"),
            "height": s.get("height"),
        })
    counts = {"approved": 0, "rejected": 0, "pending": 0}
    for s in out:
        counts[s["status"]] += 1
    return {
        "segments": out,
        "total_duration": round(total, 2),
        "approved_duration": round(approved_dur, 2),
        "counts": counts,
        "n_segments": len(out),
    }


@app.get("/clip/{seg_index}")
def clip(seg_index: int, project: str = ""):
    pdir = _media_pdir(project)
    segs = _segments_of(pdir)
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    if not seg:
        raise HTTPException(404, "segment not found")
    path = os.path.join(pdir, seg.get("clip", ""))
    if not os.path.exists(path):
        raise HTTPException(404, "clip not rendered yet")
    # FileResponse handles HTTP Range requests -> <video> seeking works
    return FileResponse(path, media_type="video/mp4")


@app.get("/thumb/{seg_index}")
def thumb(seg_index: int, project: str = ""):
    pdir = _media_pdir(project)
    segs = _segments_of(pdir)
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    if not seg:
        raise HTTPException(404, "segment not found")
    p = ensure_thumb(seg, pdir)
    if not p:
        raise HTTPException(404, "no thumbnail")
    return FileResponse(p, media_type="image/jpeg")


class StatusIn(BaseModel):
    status: str            # "approved" | "rejected" | "pending"
    note: str = ""


@app.post("/api/segments/{seg_index}/status")
def set_status(seg_index: int, body: StatusIn):
    if body.status not in ("approved", "rejected", "pending"):
        raise HTTPException(400, "invalid status")
    review = load_review()
    review[str(seg_index)] = {"status": body.status, "note": body.note}
    save_review(review)
    # M9: 🗑/✅ are DECISIONS about the video, not annotations. Before this,
    # both render and export gated purely on user_included, so a segment
    # marked "rejected" still played in the final MP4 — two controls that
    # looked like decisions, one of which did nothing. Reject now unticks
    # (out of the video); approve ticks. Nothing is deleted: the row, its
    # panel, its narration and its audio all stay, so it is one click back.
    # A row can hold SEVERAL narrations (one panel, many beats). Rejecting one
    # segment must not disturb its neighbours, so the tick is left alone and
    # video_segments() enforces the exclusion instead. Approving clears the
    # rejection; ticking the row again cannot resurrect a rejected segment.
    included = None
    if body.status == "approved":
        included = True
        segs = load_segments()
        for s in segs:
            if s["seg_index"] == seg_index and not s.get("user_included"):
                s["user_included"] = True
        _write_segments(segs)
    return {"ok": True, "seg_index": seg_index, "status": body.status,
            "user_included": included}


def video_segments(segs=None):
    """The single answer to "what is in the final video": ticked AND not
    rejected, in timeline order.

    Inclusion used to be the checkbox alone, so a 🗑 rejection clicked before
    the M9 fix (when reject was annotation-only) still shipped in the export.
    Reading review.json here makes the rejection authoritative whenever it was
    made, and keeps one rule for render, export and the counters.
    """
    segs = load_segments() if segs is None else segs
    review = load_review()
    return [s for s in segs
            if s.get("user_included")
            and (review.get(str(s["seg_index"])) or {}).get("status") != "rejected"]


class ExportIn(BaseModel):
    speed: float = 1.0     # 1.0 = normal, 1.5 = fast pass


@app.post("/api/export")
def export(body: ExportIn):
    return _do_export(speed=body.speed)


# Owner, 2026-10-04: finished videos play at 1.25x — the approve-and-render
# export uses this speed and keeps only the sped-up file. Set EXPORT_SPEED=1.0
# to go back to normal speed.
EXPORT_SPEED = float(os.environ.get("EXPORT_SPEED", "1.25"))


def _do_export(speed=1.0):
    """Concat ONLY user-included (checkbox, T3) clips, in timeline order."""
    segs = load_segments()
    approved = video_segments(segs)
    if not approved:
        raise HTTPException(400, "nothing is ticked for the final video — "
                                 "tick segments on /storyboard first")
    missing = [s["seg_index"] for s in approved
               if not os.path.exists(os.path.join(active_project_dir(), s.get("clip", "")))]
    if missing:
        raise HTTPException(400, f"ticked segments not rendered yet: {missing} "
                                 "— run render-missing first")
    # P0 (Session 25): never concatenate a timeline whose audio does not fit
    # its windows — that is how narration was being lost silently.
    _gate_timeline([s["seg_index"] for s in approved], "export")
    # E5: intro/outro title cards from project metadata (best-effort — the
    # export must never fail because a card couldn't render).
    pdir = active_project_dir()
    cards = []
    try:
        import ingest as _ing
        series, chapter = _ing.parse_series_chapter(
            json.load(open(os.path.join(pdir, "project.json"))).get("url", "")) \
            if os.path.exists(os.path.join(pdir, "project.json")) else ("", "")
        # V4 (Session 23 review): title cards showed the aggregator's hash
        # suffix ("Swordmasters Youngest Son F886A8Af") because this path
        # title-cased the raw slug. Reuse ingest's cleaner — the same one the
        # project id already uses — so the card shows just the series name.
        title = _ing.to_title_case(_ing.clean_series_slug(series or ""))
        if title:
            env = os.environ.copy()
            env.update({"HF_WORKSPACE": pdir,
                        "HF_TITLE": title,
                        "HF_SUBTITLE": f"Chapter {chapter} — RECAP",
                        "HF_OUTRO": "To be continued…"})
            subprocess.run(
                [sys.executable, os.path.join(HF, "render_segments.py"),
                 "--cards-only"], capture_output=True, text=True, env=env,
                timeout=180)
            intro = os.path.join(pdir, "clips", "intro.mp4")
            outro = os.path.join(pdir, "clips", "outro.mp4")
            cards = [os.path.exists(intro) and intro,
                     os.path.exists(outro) and outro]
    except Exception:
        cards = []
    listfile = os.path.join(active_exports_dir(), "concat.txt")
    with open(listfile, "w") as f:
        if cards and cards[0]:
            f.write(f"file '{cards[0]}'\n")
        for s in approved:
            f.write(f"file '{os.path.join(active_project_dir(), s['clip'])}'\n")
        if cards and cards[1]:
            f.write(f"file '{cards[1]}'\n")
    # timestamped name (ET): exports accumulate as history instead of
    # silently overwriting — the drawer shows every version.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    _stamp = datetime.now(ZoneInfo("America/New_York")).strftime("%b%d_%I.%M%p")
    # LongForm lesson (step 7): build the video in a hidden side folder and
    # move only the FINISHED file into exports/, so a stopped or failed export
    # never leaves a half-written video in Exports / the Publishing Studio.
    part_dir = os.path.join(active_exports_dir(), ".partial")
    os.makedirs(part_dir, exist_ok=True)
    for _old in os.listdir(part_dir):            # leftovers of a killed export
        try:
            os.remove(os.path.join(part_dir, _old))
        except OSError:
            pass
    out = os.path.join(part_dir, f"final_{_stamp}.mp4")
    # cards are stream-matched by _title_card, so always stream-copy (a
    # re-encode pads each clip's video to its audio tail — accumulating
    # frozen frames across the whole export)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
         "-c", "copy", out], check=True, capture_output=True)
    # E4: optional BGM bed — drop a licensed bgm.mp3 into the project dir
    bgm = os.path.join(pdir, "bgm.mp3")
    if os.path.exists(bgm):
        tmp = out + ".bgm.mp4"
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", out, "-stream_loop", "-1", "-i", bgm,
             "-filter_complex",
             "[1:a]volume=0.12[q];[0:a][q]amix=inputs=2:duration=first:dropout_transition=0",
             "-c:v", "copy", "-shortest", tmp], capture_output=True)
        if r.returncode == 0:
            os.replace(tmp, out)
    # LongForm lesson (step 7): YouTube plays at about -14 LUFS, so the export
    # is normalised to it (EXPORT_LUFS, 'off' to skip). Best-effort: never
    # costs the video.
    import audio_level as _al
    loud = _al.normalise(out)
    final = out
    if abs(speed - 1.0) > 1e-3:
        sped = out.replace(".mp4", f"_{speed}x.mp4")
        subprocess.run(
            [sys.executable, os.path.join(RECAP, "speed_up.py"),
             out, sped, str(speed)], check=True, capture_output=True)
        final = sped
        if os.path.exists(sped) and os.path.getsize(sped) > 0:
            try:
                os.remove(out)        # keep only the version that will be posted
            except OSError:
                pass
    done = os.path.join(active_exports_dir(), os.path.basename(final))
    os.replace(final, done)                      # appears in Exports only now
    final = done
    return {"ok": True, "clips": len(approved), "output": os.path.basename(final),
            "url": f"/export/{os.path.basename(final)}", "loudness": loud}


@app.get("/export/{name}")
def get_export(name: str, project: str = ""):
    """`project` lets the drawer serve exports from any project, not just the
    active one — the reason older exports appeared to vanish."""
    import ingest as _ing
    safe = os.path.basename(name)
    if project:
        if "/" in project or ".." in project:
            raise HTTPException(400, "bad project id")
        path = os.path.join(_ing.PROJECTS, project, "exports", safe)
    else:
        path = os.path.join(active_exports_dir(), safe)
    if not os.path.exists(path):
        raise HTTPException(404, "export not found")
    return FileResponse(path, media_type="video/mp4")


# ====================================================================
#  PHASE A — review a rendered export inside the tool
#  Deliberately separate from storyboard.json's `approved` flag, which is a
#  RENDER gate ("edits are final, clips may build"). A verdict on a finished
#  video is a different decision with a different lifetime; conflating them
#  would mean approving a video silently re-armed the render gate.
# ====================================================================
REVIEWS_NAME = "reviews.json"


def project_dir_for(project=""):
    """A project's directory by id, or the active one when blank.

    Review POSTs always carry an explicit project: an ingest can switch the
    active project underneath a reviewer mid-session.
    """
    import ingest as _ing
    if not project:
        return active_project_dir()
    if "/" in project or ".." in project:
        raise HTTPException(400, "bad project id")
    d = os.path.join(_ing.PROJECTS, project)
    if not os.path.isdir(d):
        raise HTTPException(404, "unknown project")
    return d


def cut_signature(segs=None, pdir=None):
    """Stable fingerprint of the cut an export was made from.

    Covers only what actually reaches the video — the segments video_segments()
    concatenates — and the things that change what you SEE and HEAR: order,
    duration, panel, crop, and which audio each beat plays. An mtime cannot
    tell an edited timeline from an untouched one; this can.
    """
    import hashlib
    if segs is None:
        segs = _load_segments_from(pdir) if pdir else load_segments()
    review = _load_reviews_side(pdir) if pdir else load_review()
    parts = []
    for s in segs:
        if not s.get("user_included"):
            continue
        if (review.get(str(s["seg_index"])) or {}).get("status") == "rejected":
            continue
        beats = ",".join(f"{b.get('index')}:{b.get('file') or ''}"
                         for b in s.get("beats", []))
        parts.append("|".join([
            str(s.get("seg_index")), f"{float(s.get('dur', 0)):.3f}",
            str(s.get("panel_id")), str(s.get("crop_bbox_norm")), beats]))
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def _load_segments_from(pdir):
    try:
        with open(os.path.join(pdir, "segments.json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _load_reviews_side(pdir):
    """The per-SEGMENT review.json (approve/reject), for a given project."""
    try:
        with open(os.path.join(pdir, "review.json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_reviews(pdir):
    """The per-EXPORT review records (Phase A)."""
    try:
        with open(os.path.join(pdir, REVIEWS_NAME), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_reviews(pdir, data):
    tmp = os.path.join(pdir, REVIEWS_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, os.path.join(pdir, REVIEWS_NAME))


def review_state(pdir, name, rec=None, current_sig=None):
    """Stored status plus the DERIVED superseded flag.

    superseded is never stored — it is a comparison against the cut as it
    stands right now, so it stays true after later edits without anyone having
    to remember to update the record.
    """
    recs = load_reviews(pdir)
    rec = rec if rec is not None else (recs.get(name) or {})
    sig = current_sig if current_sig is not None else cut_signature(pdir=pdir)
    status = rec.get("status") or "review_pending"
    superseded = bool(rec.get("cut_signature") and rec["cut_signature"] != sig)
    return {"status": status, "notes": rec.get("notes", ""),
            "reviewed_at": rec.get("reviewed_at"),
            "reviewed_by": rec.get("reviewed_by"),
            "cut_signature": rec.get("cut_signature"),
            "current_signature": sig, "superseded": superseded,
            "history": rec.get("history", [])}


def latest_export(project=""):
    """Newest export for a project: (name, project_id) or (None, project_id)."""
    pdir = project_dir_for(project)
    pid = os.path.basename(pdir.rstrip("/"))
    d = os.path.join(pdir, "exports")
    best, best_m = None, -1
    try:
        for f in os.listdir(d):
            if not f.endswith(".mp4"):
                continue
            m = os.stat(os.path.join(d, f)).st_mtime
            if m > best_m:
                best, best_m = f, m
    except OSError:
        pass
    return best, pid


_QC_CACHE = {}


def _qc_bundle(pdir, name):
    """Cached until the timeline, the segment review or the video changes
    (owner, 2026-10-05: "Watch the video loads very slowly" — /api/review
    took 4.7 s, almost all of it re-validating the timeline on every open)."""
    def _m(*p):
        try:
            return os.stat(os.path.join(pdir, *p)).st_mtime_ns
        except OSError:
            return 0
    key = (os.path.abspath(pdir), name, _m("segments.json"), _m("review.json"),
           _m("project.json"), _m("exports", os.path.basename(name or "")))
    hit = _QC_CACHE.get(key)
    if hit is not None:
        return hit
    out = _qc_bundle_build(pdir, name)
    if len(_QC_CACHE) > 200:
        _QC_CACHE.clear()
    _QC_CACHE[key] = out
    return out


def _qc_bundle_build(pdir, name):
    """Existing signals only — surfaced, not recomputed."""
    import storyboard_edit as _sbe
    meta = {}
    try:
        meta = json.load(open(os.path.join(pdir, "project.json")))
    except Exception:
        pass
    segs = _load_segments_from(pdir)
    side = _load_reviews_side(pdir)
    inc = [s for s in segs if s.get("user_included")
           and (side.get(str(s["seg_index"])) or {}).get("status") != "rejected"]
    holds, i = [], 0
    while i < len(inc):
        j = i
        while j + 1 < len(inc) and inc[j + 1]["panel_id"] == inc[i]["panel_id"]:
            j += 1
        if j > i:
            dur = sum(x.get("dur", 0) for x in inc[i:j + 1])
            if dur > 12.0:
                holds.append({"panel": inc[i]["panel_id"], "seconds": round(dur, 1)})
        i = j + 1
    silent = [s["seg_index"] for s in inc if not s.get("beats")]
    try:
        v = _sbe.validate_timeline(pdir)
    except Exception as e:
        v = {"ok": None, "errors": [], "warnings": [], "note": str(e)}
    mm = meta.get("match_method") or "unknown"
    job = None
    for f in os.listdir(_jobs_dir()):
        if not f.startswith("render_"):
            continue
        try:
            rec = json.load(open(os.path.join(_jobs_dir(), f)))
        except Exception:
            continue
        if rec.get("export") == name:
            job = rec
            break
    return {
        "match_method": mm,
        "semantic": ("gemini-embeddings" in mm or mm.startswith("embeddings")),
        "embed_fallback_reason": meta.get("embed_fallback_reason") or "",
        "unsplit_long_holds": meta.get("unsplit_long_holds") or [],
        "split_coverage": meta.get("split_coverage"),
        "n_pages": meta.get("n_pages"),
        "scrape_warning": meta.get("scrape_warning") or "",
        "segments_total": len(segs), "segments_in_video": len(inc),
        "runtime_s": round(sum(s.get("dur", 0) for s in inc), 1),
        "long_holds": holds, "silent_segments": silent,
        "validation": {"ok": v.get("ok"), "errors": v.get("errors", []),
                       "warnings": v.get("warnings", [])},
        "render_job": ({"ended": job.get("ended"), "clips": job.get("total"),
                        "status": job.get("status")} if job else None),
    }


def _export_stat(pdir, name):
    p = os.path.join(pdir, "exports", os.path.basename(name))
    if not os.path.exists(p):
        return None
    st = os.stat(p)
    return {"size_mb": round(st.st_size / 1e6, 1), "mtime": st.st_mtime}


@app.get("/api/review")
def api_review(project: str = "", name: str = ""):
    """Everything the review page needs, in one call."""
    pdir = project_dir_for(project)
    pid = os.path.basename(pdir.rstrip("/"))
    if not name:
        name, pid = latest_export(project or pid)
    if not name and not project:
        # Opened directly while the board's chapter has no video: answer with
        # the newest video from ANY chapter here, instead of "missing" and a
        # second round trip from the page to go and find one.
        newest = None
        for _pid, _d in _all_export_dirs():
            try:
                for f in os.listdir(_d):
                    if f.endswith(".mp4"):
                        m = os.stat(os.path.join(_d, f)).st_mtime
                        if newest is None or m > newest[0]:
                            newest = (m, _pid, f)
            except OSError:
                pass
        if newest:
            pid, name = newest[1], newest[2]
            pdir = project_dir_for(pid)
    exports = []
    d = os.path.join(pdir, "exports")
    recs = load_reviews(pdir)
    sig = cut_signature(pdir=pdir)
    try:
        for f in sorted(os.listdir(d)):
            if not f.endswith(".mp4"):
                continue
            st = os.stat(os.path.join(d, f))
            rs = review_state(pdir, f, recs.get(f), sig)
            exports.append({"name": f, "mtime": st.st_mtime,
                            "size_mb": round(st.st_size / 1e6, 1),
                            "status": rs["status"], "superseded": rs["superseded"]})
    except OSError:
        pass
    exports.sort(key=lambda e: e["mtime"], reverse=True)
    if not name:
        return {"project": pid, "name": None, "exports": [], "missing": True,
                "reason": "no export has been rendered for this project yet"}
    stat = _export_stat(pdir, name)
    return {"project": pid, "name": name, "exports": exports,
            "file_present": stat is not None, "stat": stat,
            "url": f"/export/{name}?project={pid}",
            "review": review_state(pdir, name, recs.get(name), sig),
            "qc": _qc_bundle(pdir, name)}


class ReviewIn(BaseModel):
    project: str = ""
    name: str
    status: str = ""       # approved | sent_back | review_pending ("" = notes only)
    notes: str = ""


@app.post("/api/review")
def api_review_save(body: ReviewIn):
    """Record a verdict and/or notes against ONE export.

    Notes save independently of a verdict, so a half-finished review is not
    lost. The record stamps the cut signature at decision time, which is what
    makes a later edit show up as superseded.
    """
    status = (body.status or "").strip()
    if status and status not in ("approved", "sent_back", "review_pending"):
        raise HTTPException(400, "status must be approved, sent_back or review_pending")
    pdir = project_dir_for(body.project)
    pid = os.path.basename(pdir.rstrip("/"))
    name = os.path.basename(body.name)
    if not name.endswith(".mp4"):
        raise HTTPException(400, "name must be an export filename")
    recs = load_reviews(pdir)
    rec = recs.get(name) or {"history": []}
    if status and rec.get("status") and rec["status"] != status:
        rec.setdefault("history", []).append(
            {"status": rec["status"], "notes": rec.get("notes", ""),
             "at": rec.get("reviewed_at")})
    if status:
        rec["status"] = status
    rec["notes"] = body.notes
    rec["reviewed_at"] = time.time()
    rec.setdefault("reviewed_by", None)      # placeholder for multi-user later
    stat = _export_stat(pdir, name)
    rec["export_mtime"] = stat["mtime"] if stat else rec.get("export_mtime")
    if status:                                # a verdict pins the cut it judged
        rec["cut_signature"] = cut_signature(pdir=pdir)
    recs[name] = rec
    save_reviews(pdir, recs)
    scheduled = _schedule_on_verdict(pid, name, status)
    return {"ok": True, "project": pid, "name": name,
            "review": review_state(pdir, name, rec), "scheduled": scheduled}


def _schedule_on_verdict(pid, name, status):
    """Owner, 2026-10-04: approving a video after reviewing it puts it in the
    next free posting slot; sending it back takes it off the queue."""
    import ingest as _i
    import publish_queue as _pq1
    try:
        if status == "approved":
            added, _skip = _pq1.add(_i.PROJECTS, [{"project": pid, "name": name}])
            if added:
                _ev("publish", f"{_pretty(pid)} approved → scheduled for the next free slot")
            return True
        if status in ("sent_back", "review_pending"):
            for x in _pq1.load(_i.PROJECTS)["items"]:
                if (x["project"], x["name"]) == (pid, name) and x["status"] == "queued":
                    _pq1.remove(_i.PROJECTS, x["id"])
                    _ev("publish", f"{_pretty(pid)} sent back → taken off the queue")
    except Exception as e:  # noqa — the verdict itself is saved
        print(f"[review] queue not updated: {e}", flush=True)
    return False


# ====================================================================
#  PHASE B — publish PREPARATION. Metadata + a manual upload package.
#  No OAuth, no upload, no posting. The package exists so a human can upload
#  by hand, which keeps the rights decision with a person rather than a button.
# ====================================================================
PUBLISH_NAME = "publish.json"

# YouTube's own limits, enforced here so a package is never assembled from
# metadata the platform would reject.
YT_TITLE_MAX = 100
YT_DESC_MAX = 5000
YT_TAGS_CHARS_MAX = 500
YT_PRIVACY = ("private", "unlisted", "public")
# 1 = Film & Animation, 24 = Entertainment, 31 = Anime/Animation
YT_CATEGORIES = {"1": "Film & Animation", "24": "Entertainment", "31": "Anime/Animation"}


def load_publish(pdir):
    try:
        with open(os.path.join(pdir, PUBLISH_NAME), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_publish(pdir, data):
    tmp = os.path.join(pdir, PUBLISH_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, os.path.join(pdir, PUBLISH_NAME))


import studio_settings as _studio


def _make_title_template(title: str, chapter: str) -> str:
    ch = str(chapter).strip()
    if not ch or not title:
        return title or ""
    m = re.search(r"\b(Chapter|Ch\.?|Episode|Ep\.?|Part|Pt\.?|#)\s*" + re.escape(ch) + r"\b", title, re.IGNORECASE)
    if m:
        prefix = m.group(1)
        return title[:m.start()] + prefix + " {chapter}" + title[m.end():]
    m = re.search(r"\b" + re.escape(ch) + r"\b", title)
    if m:
        return title[:m.start()] + "{chapter}" + title[m.end():]
    return title.rstrip() + " Ch. {chapter}"


def _apply_title_template(template: str, chapter: str) -> str:
    if not template:
        return ""
    return template.replace("{chapter}", str(chapter).strip())


def _make_desc_template(desc: str, chapter: str) -> str:
    ch = str(chapter).strip()
    if not ch or not desc:
        return desc or ""
    return re.sub(r"\b(Chapter|Ch\.?|Episode|Ep\.?|Part|Pt\.?|#)\s*" + re.escape(ch) + r"\b", r"\1 {chapter}", desc, flags=re.IGNORECASE)


def _apply_desc_template(template: str, chapter: str) -> str:
    if not template:
        return ""
    return template.replace("{chapter}", str(chapter).strip())


def _propagate_series_defaults(sid, series, pack):
    import ingest as _i
    root = _i.PROJECTS
    applied = 0
    t_tmpl = pack.get("title_template")
    d_tmpl = pack.get("description_template")
    tags = pack.get("default_tags")
    playlist = pack.get("playlist_id")
    privacy = pack.get("default_privacy")
    targets = pack.get("default_targets")
    extra = {k: pack[pk] for k, pk in (("category_id", "default_category"), ("made_for_kids", "default_made_for_kids"),
                                       ("synthetic_disclosure", "default_synthetic_disclosure")) if pk in pack}

    for f in os.listdir(root):
        pdir = os.path.join(root, f)
        if not os.path.isdir(pdir) or f.startswith("_"):
            continue
        try:
            csid, cseries, ch_n, cmeta = _series_ident(pdir)
            if csid != sid:
                continue
            store = load_publish(pdir)
            target_keys = list(store.keys())
            if DRAFT not in target_keys:
                target_keys.append(DRAFT)
            for k in target_keys:
                item = dict(store.get(k) or {})
                if t_tmpl:
                    item["title"] = _apply_title_template(t_tmpl, ch_n)
                if d_tmpl:
                    item["description"] = _apply_desc_template(d_tmpl, ch_n)
                if tags:
                    item["tags"] = list(tags)
                if playlist:
                    item["playlist"] = playlist
                if privacy:
                    item["privacy"] = privacy
                if targets:
                    item["targets"] = list(targets)
                item.update(extra)
                store[k] = item
            save_publish(pdir, store)
            applied += 1
        except Exception as e:
            print(f"[propagate_series_defaults] error on {f}: {e}", flush=True)

    try:
        _rerender_chosen_thumbnails(force_cover_default=True)
    except Exception as e:
        print(f"[propagate_series_defaults] thumbnail rerender error: {e}", flush=True)

    return applied


def _publish_defaults_base(pdir):
    """Sensible starting metadata from what the project already knows."""
    meta = {}
    try:
        meta = json.load(open(os.path.join(pdir, "project.json")))
    except Exception:
        pass
    series = (meta.get("series") or "").strip()
    chapter = str(meta.get("chapter") or "").strip()
    if meta.get("kind") == "range":
        import range_compile as _rc
        try:
            rtitle = _rc.title(series, meta["chapters_start"], meta["chapters_end"], 0)[0]
        except Exception:
            rtitle = f"{series} Chapter {chapter}"
        return {"title": rtitle, "description": "", "tags": [t for t in [series, "manhwa recap"] if t],
                "category_id": "1", "privacy": _studio.publish_defaults().get("privacy", "private"),
                "targets": list(_studio.publish_defaults().get("targets") or []), "publish_at": "",
                "playlist": series, "made_for_kids": False, "synthetic_disclosure": True, "thumbnail": None}
    # Spec 06 B1: hook first, from the chapter's own narration until the SEO
    # writer's hook replaces it. No narration -> no hook, and the validator says so.
    import chapter_title as _ct
    hook = ""
    try:
        with open(os.path.join(pdir, "script.txt"), encoding="utf-8") as f:
            hook = _ct.hook_from_content(f.read(2000))
    except OSError:
        pass
    title = _ct.build(hook, series, chapter, YT_TITLE_MAX, _studio.title_template()) if series else "Chapter Recap"
    import series_pack as _sp
    sid, _, _, _ = _series_ident(pdir)
    sp = _sp.load(_yt_root(), sid) if sid else None
    base_tags = [t for t in [series, "recap", "manhwa"] if t]
    if sp and sp.get("default_tags"):
        base_tags = list(dict.fromkeys(base_tags + sp["default_tags"]))

    if sp and sp.get("title_template"):
        title = _apply_title_template(sp["title_template"], chapter)

    if sp and sp.get("description_template"):
        description = _apply_desc_template(sp["description_template"], chapter)
    else:
        description = (f"A recap of {series} chapter {chapter}."
                        if series else "A chapter recap.")

    return {
        "title": title[:YT_TITLE_MAX],
        "description": description,
        "tags": base_tags,
        "category_id": (sp.get("default_category") if sp else None) or "1",
        # Owner, 2026-10-04: default channel 🦩 Flamingo Remix (mk:youtube).
        # Privacy is private unless the owner saved "public" in ⚙️ Settings &
        # Channels (studio_settings.py) — never public by omission.
        "privacy": (sp.get("default_privacy") if sp else None) or _studio.publish_defaults().get("privacy", "private"),
        "targets": list((sp.get("default_targets") if sp else None) or _studio.publish_defaults().get("targets") or []),
        "publish_at": "",
        "playlist": (sp.get("playlist_id") if sp else None) or series,
        "made_for_kids": bool((sp or {}).get("default_made_for_kids", False)),
        # The narration is synthetic speech, so this starts TRUE — YouTube
        # requires disclosure of realistic altered or synthetic content.
        "synthetic_disclosure": bool((sp or {}).get("default_synthetic_disclosure", True)),
        "thumbnail": None,
    }


DRAFT = "_draft"     # per-chapter publish details prepared BEFORE a render (owner, 2026-10-04)
_DRAFT_FIELDS = ("title", "description", "tags", "category_id", "playlist", "made_for_kids", "synthetic_disclosure")


def publish_defaults(pdir):
    """Starting metadata for a video: what the project knows, then anything the
    owner prepared for the chapter before it was rendered (the draft)."""
    md = _publish_defaults_base(pdir)
    try:
        draft = load_publish(pdir).get(DRAFT) or {}
    except Exception:
        draft = {}
    for k in _DRAFT_FIELDS:
        if draft.get(k) not in (None, "", []):
            md[k] = draft[k]
    return md


def validate_publish(md, pdir=None, name=None):
    """Platform-limit problems, as a list of human sentences ([] == fine).

    With the export's project, the chapter-title rules of spec 06 B1 apply too
    (chapter_title.problems): hook first, no old "P{n} | … | #manhwa"
    pattern, no title identical to another export's."""
    out = []
    title = (md.get("title") or "").strip()
    if not title:
        out.append("A title is required.")
    elif len(title) > YT_TITLE_MAX:
        out.append(f"Title is {len(title)} characters; the limit is {YT_TITLE_MAX}.")
    meta = (_read_json(os.path.join(pdir, "project.json")) or {}) if pdir else {}
    if pdir and md.get("seo_v2"):
        # Spec 10 §9 Stage 3/4: failures block approval and posting
        import chapter_seo as _cseo
        try:
            _pk = _series_pack(pdir, refresh=False) or {}
            _sid, _sr, _n, _m = _series_ident(pdir)
            if _n is not None:
                out += _cseo.validate(md, _pk.get("series_name_en") or _sr, _n)
        except Exception:
            pass
    if title and pdir and meta.get("kind") == "range":
        import range_compile as _rc
        out += [p for p in _rc.problems(title, meta) if not p.startswith("Title is ")]
    elif title and pdir:
        import chapter_title as _ct
        import ingest as _ing
        pid = os.path.basename(pdir.rstrip("/"))
        others = _ct.existing_titles(_ing.PROJECTS, skip_project=pid)
        # Within this chapter, re-renders share one title by design; only an
        # export that actually went out (uploads.json / publishes.json) counts.
        went = set(_read_json(os.path.join(pdir, UPLOADS_NAME)) or {}) | \
            set(_read_json(os.path.join(pdir, "publishes.json")) or {})
        mine = load_publish(pdir)
        others += [m.get("title") for n, m in mine.items()
                   if n != name and n in went and isinstance(m, dict)]
        out += [p for p in _ct.problems(title, (meta.get("series") or "").strip(),
                                        str(meta.get("chapter") or ""), others)
                if not p.startswith("Title is ")]
    if pdir and md.get("summary_block"):
        # Spec 06 B2: block 2 must be written for THIS chapter — >80% word
        # 3-gram overlap with the previous chapter's is the templating signal.
        import description_blocks as _db
        prev = _prev_summary_block(pdir, name)
        ov = _db.block2_overlap(md["summary_block"], prev) if prev else 0.0
        if ov > _db.OVERLAP_MAX:
            out.append(f"The chapter summary (description block 2) repeats {int(round(ov * 100))}% of the "
                       "previous chapter's — rewrite it for this chapter (YouTube reads repeats as templated).")
    if len(md.get("description") or "") > YT_DESC_MAX:
        out.append(f"Description is over the {YT_DESC_MAX}-character limit.")
    tags = md.get("tags") or []
    if not isinstance(tags, list):
        out.append("Tags must be a list.")
    else:
        total = sum(len(t) for t in tags) + max(0, len(tags) - 1)
        if total > YT_TAGS_CHARS_MAX:
            out.append(f"Tags total {total} characters; the limit is {YT_TAGS_CHARS_MAX}.")
    if md.get("privacy") not in YT_PRIVACY:
        out.append(f"Privacy must be one of {', '.join(YT_PRIVACY)}.")
    if str(md.get("category_id")) not in YT_CATEGORIES:
        out.append("Pick a category.")
    if md.get("publish_at") and md.get("privacy") != "private":
        out.append("A scheduled publish time requires privacy to be private.")
    return out


def publish_readiness(pdir, name):
    """Whether a package may be assembled at all, and why not.

    Preparation is gated on the REVIEW verdict: a package is a step towards
    publishing, and publishing an unreviewed or stale cut is the mistake this
    whole phase exists to prevent.
    """
    rv = review_state(pdir, name)
    blockers = []
    if rv["status"] != "approved":
        blockers.append("This export has not been approved in Review yet.")
    if rv["superseded"]:
        blockers.append("The cut changed after this export was approved — "
                        "re-render and re-review before preparing it.")
    return {"review_status": rv["status"], "superseded": rv["superseded"],
            "blockers": blockers, "ready": not blockers}


def _publish_payload(pdir, pid, name, md):
    """The one response shape the publish form is built from."""
    import thumbnail as _tb
    thumb = _tb.get(pdir, name)
    return {"project": pid, "name": name, "metadata": md,
            "problems": validate_publish(md, pdir, name),
            "readiness": publish_readiness(pdir, name),
            "seo": (__import__("seo").get(pdir, name, current_signature=cut_signature(pdir=pdir))
                    or __import__("seo").get(pdir, DRAFT) or None),
            "youtube_configured": __import__("yt_api").configured(),
            "thumbnail": thumb,
            "thumbcopilot": _thumb_state(pdir, name),
            "thumbnail_note": _tb.publish_note(bool(thumb)),
            "thumbnail_limits": {"max_bytes": _tb.MAX_BYTES,
                                 "formats": list(_tb.ALLOWED),
                                 "min_width": _tb.MIN_WIDTH,
                                 "ideal": list(_tb.IDEAL)},
            "preparing": (pid, name) in _PREPARING,
            "prepare_note": _PREP_NOTE.get((pid, name)),
            "categories": YT_CATEGORIES, "privacy_options": list(YT_PRIVACY),
            "limits": {"title": YT_TITLE_MAX, "description": YT_DESC_MAX,
                       "tags_chars": YT_TAGS_CHARS_MAX}}


@app.get("/api/publish")
def api_publish(project: str = "", name: str = ""):
    pdir = project_dir_for(project)
    pid = os.path.basename(pdir.rstrip("/"))
    if not name:
        name, pid = latest_export(project or pid)
    if not name:
        return {"project": pid, "name": None, "missing": True,
                "reason": "no export has been rendered for this project yet"}
    store = load_publish(pdir)
    md = {**publish_defaults(pdir), **(store.get(name) or {})}
    return _publish_payload(pdir, pid, name, md)


class ThumbIn(BaseModel):
    project: str = ""
    name: str


class PublishIn(BaseModel):
    project: str = ""
    name: str
    metadata: dict


class SeriesDefaultsIn(BaseModel):
    project: str
    name: str = ""
    metadata: dict = {}
    apply_all: bool = True


@app.post("/api/publish/series_defaults")
def api_publish_series_defaults(body: SeriesDefaultsIn):
    """Save title format, description template, tags, playlist, privacy,
    targets and thumbnail look as series defaults, and apply across all chapters."""
    pdir = project_dir_for(body.project)
    sid, series, chapter_n, meta = _series_ident(pdir)
    if not sid:
        raise HTTPException(400, "project has no series")
    import series_pack as _sp
    import thumbnail_studio as tstudio
    root = _yt_root()
    pack = _series_pack(pdir) or {}
    md = body.metadata or {}

    title = (md.get("title") or "").strip()
    if title:
        pack["title_template"] = _make_title_template(title, chapter_n)

    desc = (md.get("description") or "").strip()
    if desc:
        pack["description_template"] = _make_desc_template(desc, chapter_n)

    if md.get("tags"):
        tags = md["tags"] if isinstance(md["tags"], list) else [t.strip() for t in str(md["tags"]).split(",") if t.strip()]
        pack["default_tags"] = list(dict.fromkeys(tags))
    if md.get("playlist"):
        pack["playlist_id"] = str(md["playlist"]).strip()
    if md.get("privacy"):
        pack["default_privacy"] = str(md["privacy"])
    if md.get("targets"):
        pack["default_targets"] = list(md["targets"])
    # "for most things if not all" (owner, 2026-10-07)
    if md.get("category_id"):
        pack["default_category"] = str(md["category_id"])
    if "made_for_kids" in md:
        pack["default_made_for_kids"] = bool(md["made_for_kids"])
    if "synthetic_disclosure" in md:
        pack["default_synthetic_disclosure"] = bool(md["synthetic_disclosure"])

    # Lock thumbnail style for the series
    key = tstudio.series_key(meta)
    style = tstudio.load_style(_yt_root(), key)
    chosen_comp = None
    all_recs = tstudio.load_concepts(pdir)
    rec = all_recs.get(body.name or DRAFT) or {}
    if rec.get("chosen"):
        chosen_comp = rec["chosen"].get("composition")
    if chosen_comp:
        tstudio.approve_style(_yt_root(), key, style, composition=chosen_comp)
    else:
        tstudio.approve_style(_yt_root(), key, style)

    style = tstudio.load_style(_yt_root(), key)
    pack["thumbnail_dna"] = {"composition": style.get("composition"), "palette": style.get("palette"),
                             "badge": style.get("badge"), "approved": True}

    _sp.save(root, pack)

    applied = 0
    if body.apply_all:
        applied = _propagate_series_defaults(sid, series, pack)

    _ev("publish", f"{series}: saved series defaults & applied to {applied} chapters", "ok")
    return {"ok": True, "series": series, "pack": pack, "applied": applied}


@app.post("/api/publish")
def api_publish_save(body: PublishIn):
    pdir = project_dir_for(body.project)
    name = os.path.basename(body.name)
    if not name.endswith(".mp4") and name != DRAFT:
        raise HTTPException(400, "name must be an export filename")
    md = {**publish_defaults(pdir), **(body.metadata or {})}
    if isinstance(md.get("tags"), str):
        md["tags"] = [t.strip() for t in md["tags"].split(",") if t.strip()]
    md["privacy"] = md.get("privacy") or "private"
    store = load_publish(pdir)
    store[name] = md
    save_publish(pdir, store)
    pid = os.path.basename(pdir.rstrip("/"))
    # Same shape as the GET. Returning a thinner object here emptied the
    # category and privacy dropdowns the moment anything was saved, because the
    # page assigns this response straight over its state.
    return {"ok": True, **_publish_payload(pdir, pid, name, md)}


@app.get("/api/publish/package")
def api_publish_package(project: str = "", name: str = ""):
    """A zip a human can upload by hand: metadata, a checklist, the thumbnail.

    The VIDEO is not bundled — exports run to hundreds of MB and are already
    downloadable from /export. The package carries everything you would
    otherwise retype into the upload form.
    """
    import io
    import zipfile
    from fastapi.responses import Response
    pdir = project_dir_for(project)
    pid = os.path.basename(pdir.rstrip("/"))
    if not name:
        name, pid = latest_export(project or pid)
    if not name:
        raise HTTPException(404, "no export to package")
    ready = publish_readiness(pdir, name)
    if not ready["ready"]:
        raise HTTPException(409, "not ready to package — " + " ".join(ready["blockers"]))
    store = load_publish(pdir)
    md = {**publish_defaults(pdir), **(store.get(name) or {})}
    problems = validate_publish(md, pdir, name)
    if problems:
        raise HTTPException(400, "fix the metadata first: " + " ".join(problems))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("metadata.json", json.dumps(md, indent=2))
        lines = [
            f"UPLOAD PACKAGE — {name}",
            f"project: {pid}",
            "",
            "Paste these into the YouTube upload form:",
            "",
            f"TITLE:\n{md['title']}",
            "",
            f"DESCRIPTION:\n{md['description']}",
            "",
            f"TAGS: {', '.join(md.get('tags') or [])}",
            f"CATEGORY: {YT_CATEGORIES.get(str(md.get('category_id')), '?')}",
            f"PRIVACY: {md.get('privacy')}",
            f"SCHEDULED: {md.get('publish_at') or '(none)'}",
            f"PLAYLIST: {md.get('playlist') or '(none)'}",
            f"MADE FOR KIDS: {'yes' if md.get('made_for_kids') else 'no'}",
            f"SYNTHETIC/ALTERED CONTENT DISCLOSURE: "
            f"{'yes' if md.get('synthetic_disclosure') else 'no'}",
            "",
            "The video file itself is not in this zip — download it from the",
            "Exports tab. Thumbnail (if chosen) is included as thumbnail.png.",
            "",
            "BEFORE YOU UPLOAD: confirm you have the rights to publish this",
            "artwork. The pipeline sources pages from aggregators, which are",
            "not the rights holder.",
        ]
        z.writestr("upload-checklist.txt", "\n".join(lines))
        thumb = md.get("thumbnail") or {}
        tp = None
        if thumb.get("type") == "panel" and thumb.get("panel_id"):
            tp = os.path.join(pdir, "crops", f"{thumb['panel_id']}.png")
        elif thumb.get("type") == "segment" and thumb.get("seg_index") is not None:
            segs = _load_segments_from(pdir)
            sg = next((x for x in segs
                       if x["seg_index"] == thumb["seg_index"]), None)
            tp = (sg or {}).get("panel_file")
        if tp and os.path.exists(tp):
            z.write(tp, "thumbnail.png")
    buf.seek(0)
    fn = name.replace(".mp4", "") + "_upload_package.zip"
    return Response(content=buf.read(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{fn}"'})


# ====================================================================
#  Series Thumbnail Copilot
#  Two lifetimes: a STYLE PACK per series (inherited by every later chapter
#  once approved) and CONCEPTS per export. Applying a concept renders a real
#  1280x720 file and hands it to the EXISTING thumbnail store, so the publish
#  workflow and the manual upload path are untouched.
# ====================================================================
class ThumbGenIn(BaseModel):
    project: str = ""
    name: str
    new_style: bool = False           # deliberately re-derive the series look
    composition: str = ""             # override the composition family


class ThumbApplyIn(BaseModel):
    project: str = ""
    name: str
    concept_id: str
    overlay_text: str = None          # operator can rewrite the hook
    set_series_default: bool = False  # approve this look for the whole series


def _thumb_state(pdir, name):
    """One shape for every thumbnail endpoint, so the panel cannot disagree
    with itself."""
    import thumbnail_studio as tstudio
    import thumbnail as _tb
    meta = _read_json(os.path.join(pdir, "project.json")) or {}
    key = tstudio.series_key(meta)
    style = tstudio.load_style(_yt_root(), key)
    sig = cut_signature(pdir=pdir)
    rec = tstudio.get_concepts(pdir, name, current_signature=sig)
    return {
        "project": os.path.basename(pdir.rstrip("/")), "name": name,
        "series_key": key, "series": meta.get("series") or "",
        "chapter": str(meta.get("chapter") or ""),
        "style": style or None,
        "style_approved": bool((style or {}).get("approved")),
        "concepts": rec or None,
        "current_signature": sig,
        "current_thumbnail": _tb.get(pdir, name) or None,
        "fonts_ok": tstudio.font_available(),
    }


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@app.get("/api/thumbcopilot")
def api_thumbcopilot(project: str = "", name: str = ""):
    pdir = project_dir_for(project)
    if not name:
        name, _pid = latest_export(project or os.path.basename(pdir.rstrip("/")))
    if not name:
        return {"missing": True, "reason": "no export has been rendered yet"}
    return _thumb_state(pdir, name)


def _ensure_project_cover(pdir):
    """Put the series' real cover (the one Library shows) into this chapter as
    pages/_cover.<ext>, so "Cover art + chapter number" is the actual cover
    (owner, 2026-10-06: Extra's Academy ch.114's "cover" was a story panel —
    the chapter had no cover file and the design silently drew a panel).
    Returns the path, or "" when the series has no cover."""
    import glob as _g
    import shutil as _sh
    import thumbnail_studio as tstudio
    have = _g.glob(os.path.join(pdir, "pages", tstudio.COVER_NAME + ".*"))
    if have:
        return have[0]
    try:
        import watchlist as _wl
        meta = _read_json(os.path.join(pdir, "project.json"))
        sx, _m = _wl.find_by_mirror(_wl.load(_wl_root()), meta.get("url") or "")
        if not sx:
            return ""
        try:
            api_watchlist_cover(sx["id"])               # fetches and caches it if needed
        except HTTPException:
            return ""
        for ext in ("jpg", "png", "webp"):
            src = os.path.join(_wl_root(), "_covers", f"{sx['id']}.{ext}")
            if os.path.exists(src):
                os.makedirs(os.path.join(pdir, "pages"), exist_ok=True)
                dst = os.path.join(pdir, "pages", f"{tstudio.COVER_NAME}.{ext}")
                _sh.copyfile(src, dst)
                return dst
    except Exception:
        return ""
    return ""


class ThumbPreviewIn(BaseModel):
    project: str
    name: str = ""
    composition: str = ""


@app.post("/api/thumbcopilot/preview")
def api_thumbcopilot_preview(body: ThumbPreviewIn):
    """Render the spec-07 DEFAULT thumbnail (round 0) for a chapter into
    thumbnails/_preview/ WITHOUT touching its saved options or its chosen
    thumbnail — for side-by-side comparison. Runs the (cached, metered)
    face pass; nothing else costs anything. Returns the selection report."""
    import thumbnail_studio as tstudio
    pdir = project_dir_for(body.project)
    _ensure_project_cover(pdir)
    meta = _read_json(os.path.join(pdir, "project.json"))
    name = os.path.basename(body.name or "") or (latest_export(body.project)[0] or "")
    store = load_publish(pdir)
    title = ({**publish_defaults(pdir), **(store.get(name) or {})}).get("title") or ""
    key = tstudio.series_key(meta)
    style = tstudio.load_style(_yt_root(), key) or tstudio.build_style_pack(pdir, meta)
    bible = None
    try:
        import series_bible as _sbib
        import ingest as _ing
        slug, _c = _ing.parse_series_chapter(meta.get("url") or "")
        bible = _sbib.load_series_bible(slug, pdir) if slug else None
    except Exception:
        bible = None
    face_note = ""
    try:
        import gemini_tts as _gt
        tstudio.ensure_face_boxes(pdir, bible=bible, api_key=_gt.env_any_case("GEMINI_API_KEY"))
    except usage.UsageCapExceeded as e:
        face_note = "face check waits for budget: " + str(e)[:120]
    except Exception as e:  # noqa
        face_note = "face check failed: " + str(e)[:160]
    _panels, report = tstudio.rank_panels_report(pdir, 8, bible=bible)
    cs = tstudio.rank_concepts(tstudio.build_concepts(pdir, meta, style, title, bible=bible), style, title)
    if not cs:
        raise HTTPException(422, "no usable panels for a thumbnail")
    c = next((x for x in cs if x["composition"] == body.composition), None) if body.composition else None
    c = c or cs[0]
    out = os.path.join(pdir, "thumbnails", "_preview", f"spec07_{c['composition']}.png")
    r = tstudio.render_concept(pdir, c, style, out)
    return {"ok": True, "file": os.path.relpath(out, pdir), "composition": c["composition"], "title_used": title,
            "focal_panel": c.get("focal_panel"), "face_box": c.get("face_box"), "mirrored": r.get("mirrored"),
            "arrow": c.get("_arrow"), "hook": c.get("_hook_layout"), "cover_trim": c.get("_cover_trim"),
            "font": tstudio.font_path(), "selection": report, "face_note": face_note,
            "lookalike": {"bits": tstudio.LOOKALIKE_BITS, "rgb": tstudio.LOOKALIKE_RGB},
            "options": [x["composition"] for x in cs]}


@app.get("/api/thumbcopilot/preview.png")
def api_thumbcopilot_preview_png(project: str, composition: str = "panel-hero"):
    pdir = project_dir_for(project)
    p = os.path.join(pdir, "thumbnails", "_preview", f"spec07_{os.path.basename(composition)}.png")
    if not os.path.exists(p):
        raise HTTPException(404, "no preview rendered")
    return FileResponse(p, media_type="image/png")


@app.post("/api/thumbcopilot/generate")
def api_thumbcopilot_generate(body: ThumbGenIn):
    """Concepts for this chapter, built on the series' approved style.

    Deterministic and local: no model call, no network. The concepts are
    assembled from panels this project already produced, so generating is free
    and always matches the chapter.
    """
    import thumbnail_studio as tstudio
    pdir = project_dir_for(body.project)
    name = os.path.basename(body.name or "")
    if not name:
        raise HTTPException(400, "which export are these concepts for?")
    _ensure_project_cover(pdir)
    meta = _read_json(os.path.join(pdir, "project.json"))
    key, style = tstudio.ensure_style(_yt_root(), pdir, meta, force=body.new_style)
    if body.composition and body.composition in tstudio.COMPOSITIONS:
        style = dict(style, composition=body.composition)
        tstudio.save_style(_yt_root(), key, style)

    # The chosen publish title is the verbal hook; the thumbnail echoes it.
    store = load_publish(pdir)
    md = {**publish_defaults(pdir), **(store.get(name) or {})}
    title = md.get("title") or ""

    # The series lead's close-up comes from the Series Bible's names; a
    # Regenerate offers the next-best panels rather than the same picks.
    bible = None
    try:
        import series_bible as _sbib
        import ingest as _ing
        slug, _c = _ing.parse_series_chapter(meta.get("url") or "")
        bible = _sbib.load_series_bible(slug, pdir) if slug else None
    except Exception:
        bible = None
    # Spec 07 §5: one metered vision pass per chapter for face/bubble/logo boxes
    # (owner-approved 2026-10-06, analysis only — nothing is generated).
    # Cached per panel; over the spend limit the panels rank without faces.
    face_note = ""
    try:
        import gemini_tts as _gt
        tstudio.ensure_face_boxes(pdir, bible=bible, api_key=_gt.env_any_case("GEMINI_API_KEY"))
    except usage.UsageCapExceeded as e:
        face_note = "face check waits for budget: " + str(e)[:120]
    except Exception as e:  # noqa
        face_note = "face check failed: " + str(e)[:160]
    if face_note:
        _ev("publish", f"{os.path.basename(pdir)}: thumbnail {face_note}", "warn")
    prev = tstudio.get_concepts(pdir, name) or {}
    used = [c.get("focal_panel") for c in prev.get("concepts") or []] if not body.new_style else []
    rnd = 0 if body.new_style or not prev else int(prev.get("round", 0)) + 1
    concepts = tstudio.rank_concepts(
        tstudio.build_concepts(pdir, meta, style, title, exclude=used, bible=bible, round_=rnd), style, title)
    if not concepts and used:          # every panel used up: start again from the best
        rnd = 0
        concepts = tstudio.rank_concepts(
            tstudio.build_concepts(pdir, meta, style, title, bible=bible, round_=0), style, title)
    if not concepts:
        raise HTTPException(422, "no usable panels found for this project — "
                                 "the thumbnail copilot needs extracted panels")
    rec = {
        "concepts": concepts,
        "series_key": key,
        "style_version": style.get("version"),
        "inherited_series_style": bool(style.get("approved")),
        "title_used": title,
        "round": rnd,
        "chapter": str(meta.get("chapter") or ""),
        "confidence": _thumb_confidence(concepts, style, title),
        "cut_signature": cut_signature(pdir=pdir),
        "generated_at": time.time(),
        "chosen": prev.get("chosen"),          # an applied choice survives
        "face_note": face_note,
    }
    tstudio.put_concepts(pdir, name, rec)
    return _thumb_state(pdir, name)


def _thumb_confidence(concepts, style, title):
    pts, why = 0, []
    top = concepts[0]["score"]["total"] if concepts else 0
    if top >= 70:
        pts += 35; why.append("a strong focal panel was found")
    elif top >= 45:
        pts += 22; why.append("a usable focal panel was found")
    else:
        why.append("no panel scores well as a thumbnail (-35)")
    if (style or {}).get("approved"):
        pts += 30; why.append("inherits the approved series style")
    else:
        pts += 12; why.append("series style is still a draft")
    if title:
        pts += 20; why.append("aligned to the chosen title")
    else:
        why.append("no publish title chosen yet (-20)")
    if (style or {}).get("anchor_images"):
        pts += 15; why.append("series anchor artwork available")
    else:
        why.append("no cover/anchor art found (-15)")
    pts = max(0, min(100, pts))
    return {"score": pts, "reasons": why,
            "band": "high" if pts >= 75 else ("medium" if pts >= 50 else "low")}


@app.get("/thumbconcept")
def thumbconcept(project: str = "", name: str = "", concept_id: str = ""):
    """Render a concept for PREVIEW only — never touches the publish state."""
    import thumbnail_studio as tstudio
    pdir = project_dir_for(project)
    rec = tstudio.get_concepts(pdir, name)
    c = next((x for x in (rec.get("concepts") or [])
              if x.get("id") == concept_id), None)
    if not c:
        raise HTTPException(404, "no such concept")
    key = tstudio.series_key(_read_json(os.path.join(pdir, "project.json")))
    style = tstudio.load_style(_yt_root(), key)
    out = os.path.join(pdir, "exports", "_thumbs", "preview_%s_%s.jpg"
                       % (os.path.basename(name), concept_id))
    tstudio.render_concept(pdir, c, style, out)
    return FileResponse(out, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


def _rerender_chosen_thumbnails(force_cover_default=True):
    """Redraw every video's thumbnail with the approved series style (or latest layout).
    When force_cover_default=True, it applies the series approved cover composition
    (cover-badge / cover-center) to every chapter that has an export."""
    import ingest as _i
    import thumbnail_studio as tstudio
    n, skipped = 0, 0
    for pid in sorted(os.listdir(_i.PROJECTS)):
        pdir = os.path.join(_i.PROJECTS, pid)
        if pid.startswith("_") or not os.path.isdir(os.path.join(pdir, "exports")):
            continue
        _ensure_project_cover(pdir)
        meta = _read_json(os.path.join(pdir, "project.json"))
        key = tstudio.series_key(meta)
        style = tstudio.load_style(_yt_root(), key)
        target_comp = (style or {}).get("composition") if (style or {}).get("approved") else "cover-badge"
        for name in sorted(os.listdir(os.path.join(pdir, "exports"))):
            if not name.endswith(".mp4"):
                continue
            try:
                # Regenerate concepts with fresh cover and new options
                api_thumbcopilot_generate(ThumbGenIn(project=pid, name=name))
                cs = (tstudio.get_concepts(pdir, name) or {}).get("concepts") or []
                cid = None
                if target_comp:
                    cid = next((c["id"] for c in cs if c.get("composition") == target_comp), None)
                if not cid:
                    rec_c = next((c for c in cs if c.get("recommended")), cs[0] if cs else None)
                    cid = rec_c["id"] if rec_c else None
                if cid:
                    api_thumbcopilot_apply(ThumbApplyIn(project=pid, name=name, concept_id=cid))
                    n += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"[rerender] error on {pid}/{name}: {e}", flush=True)
                skipped += 1
    _ev("publish", f"thumbnails updated retroactively with series style: {n}" + (f" ({skipped} skipped)" if skipped else ""), "ok")
    return n


@app.post("/api/thumbcopilot/rerender_all")
def api_thumbcopilot_rerender_all():
    threading.Thread(target=_rerender_chosen_thumbnails, daemon=True).start()
    return {"ok": True, "note": "redrawing in the background — Activity shows when it's done"}


@app.post("/api/thumbcopilot/apply_retroactive")
def api_thumbcopilot_apply_retroactive():
    threading.Thread(target=_rerender_chosen_thumbnails, daemon=True).start()
    return {"ok": True, "note": "applying cover thumbnail style retroactively to all chapters in background"}


@app.post("/api/thumbcopilot/apply")
def api_thumbcopilot_apply(body: ThumbApplyIn):
    """Render the chosen concept and hand it to the EXISTING thumbnail store.

    This is the only bridge between the copilot and the publish workflow: the
    file goes through thumbnail.save(), the same path a manual upload takes, so
    everything downstream — validation, preview, delete, the publish note —
    keeps working unchanged.
    """
    import thumbnail_studio as tstudio
    import thumbnail as _tb
    pdir = project_dir_for(body.project)
    name = os.path.basename(body.name or "")
    rec = tstudio.get_concepts(pdir, name)
    c = next((x for x in (rec.get("concepts") or [])
              if x.get("id") == body.concept_id), None)
    if not c:
        raise HTTPException(404, "no such concept — generate first")
    c = dict(c)
    if body.overlay_text is not None:
        v = tstudio.validate_hook(body.overlay_text)
        c["overlay_text"] = v["text"]

    key = tstudio.series_key(_read_json(os.path.join(pdir, "project.json")))
    style = tstudio.load_style(_yt_root(), key)
    tmp = os.path.join(pdir, "exports", "_thumbs", "apply_%s.jpg" % body.concept_id)
    try:
        tstudio.render_concept(pdir, c, style, tmp)
    except ValueError as e:                    # QA refused it (e.g. no real cover)
        raise HTTPException(409, str(e))
    with open(tmp, "rb") as f:
        data = f.read()
    try:
        saved = _tb.save(pdir, name, data)      # the existing store, unchanged
    except _tb.ThumbnailError as e:
        raise HTTPException(422, str(e))

    if body.set_series_default:
        tstudio.approve_style(_yt_root(), key, style,
                              composition=c.get("composition"))

    all_recs = tstudio.load_concepts(pdir)
    if name in all_recs:
        all_recs[name]["chosen"] = {
            "concept_id": c.get("id"), "name": c.get("name"),
            "overlay_text": c.get("overlay_text"),
            "composition": c.get("composition"),
            "focal_panel": c.get("focal_panel"),
            "used_series_style": bool(c.get("follows_series_style")),
            "at": time.time()}
        tstudio.save_concepts(pdir, all_recs)
    out = _thumb_state(pdir, name)
    out["applied"] = saved
    return out


@app.post("/api/thumbcopilot/style/approve")
def api_thumbcopilot_approve(body: ThumbGenIn):
    """Lock this look in as the series default for every later chapter."""
    import thumbnail_studio as tstudio
    pdir = project_dir_for(body.project)
    meta = _read_json(os.path.join(pdir, "project.json"))
    key, style = tstudio.ensure_style(_yt_root(), pdir, meta)
    if body.composition and body.composition in tstudio.COMPOSITIONS:
        style["composition"] = body.composition
    tstudio.approve_style(_yt_root(), key, style)
    return _thumb_state(pdir, os.path.basename(body.name or ""))


# ====================================================================
#  SEO Copilot — YouTube metadata suggestions for one export
#  Project truth first (seo.truth_card, local + deterministic), then the
#  channel's MEASURED style, then bounded competitor research, then one gated
#  model call. Suggestions are stored per export and never written into the
#  publish metadata unless the operator applies them.
# ====================================================================
class SeoGenIn(BaseModel):
    project: str = ""
    name: str
    refresh_style: bool = False       # force a channel re-fetch


class SeoApplyIn(BaseModel):
    project: str = ""
    name: str
    field: str                        # title | description | tags | hashtags
    value: object = None              # explicit value (a chosen title)
    variant: str = ""                 # "short" for the shorter description


def _seo_state(pdir, name):
    """One shape for GET and POST, so the panel cannot disagree with itself —
    the same mistake that once left the publish dropdowns empty."""
    import seo as _seo
    sig = cut_signature(pdir=pdir)
    rec = _seo.get(pdir, name, current_signature=sig)
    return {"project": os.path.basename(pdir.rstrip("/")), "name": name,
            "seo": rec or None, "current_signature": sig,
            "youtube_configured": __import__("yt_api").configured()}


@app.get("/api/series_bible")
def api_get_series_bible(project: str = "", series: str = ""):
    import sys
    _RECAP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _RECAP not in sys.path:
        sys.path.insert(0, _RECAP)
    import series_bible
    pdir = project_dir_for(project) if project else active_project_dir()
    series_id = series
    if not series_id and pdir and os.path.isdir(pdir):
        meta = load_meta(pdir)
        series_id = meta.get("series") or os.path.basename(pdir).split("_")[0]
    bible = series_bible.load_series_bible(series_id, pdir=pdir)
    return {"ok": bool(bible), "series_id": series_id, "bible": bible}


@app.post("/api/series_bible")
def api_save_series_bible(body: dict):
    import sys
    _RECAP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _RECAP not in sys.path:
        sys.path.insert(0, _RECAP)
    import series_bible
    project = body.get("project", "")
    pdir = project_dir_for(project) if project else active_project_dir()
    bible = body.get("bible") or body
    series_id = body.get("series_id") or bible.get("series_id")
    if not series_id:
        raise HTTPException(400, "series_id is required")
    saved_path = series_bible.save_series_bible(series_id, bible, pdir=pdir)
    return {"ok": True, "path": saved_path, "bible": bible}


@app.get("/api/seo")
def api_seo(project: str = "", name: str = ""):
    pdir = project_dir_for(project)
    if not name:
        name, _pid = latest_export(project or os.path.basename(pdir.rstrip("/")))
    if not name:
        return {"missing": True, "reason": "no export has been rendered yet"}
    return _seo_state(pdir, name)


@app.post("/api/seo/generate")
def api_seo_generate(body: SeoGenIn):
    """Generate suggestions for one export.

    Runs even when YouTube is unconfigured or failing: the truth card is local,
    so the copilot degrades to lower confidence rather than refusing. That is
    the documented behaviour — never invent identity, but never stall either.
    """
    import seo as _seo
    import yt_api
    pdir = project_dir_for(body.project)
    name = os.path.basename(body.name or "")
    if not name:
        raise HTTPException(400, "which export are these suggestions for?")

    _seo_stage(pdir, name, "reading the chapter")
    card = _seo.truth_card(pdir)                     # 1. project truth FIRST
    _seo_stage(pdir, name, "web research (Google)")
    # 2. web research about the series (Google-grounded, sourced, cached a week)
    web, web_err = None, None
    try:
        import seo_research as _sw
        import gemini_tts as _gt
        _meta = _read_json(os.path.join(pdir, "project.json"))
        web = _sw.get(_yt_root(), card.get("series"), card.get("aliases") or [], _meta.get("url") or "",
                      _gt.env_any_case("GEMINI_API_KEY"), force=body.refresh_style)
        if web:
            card["web_facts"] = web.get("facts") or {}
    except usage.UsageCapExceeded as e:
        web_err = f"skipped — {str(e)[:120]}"
    except Exception as e:  # noqa — SEO carries on without it
        web_err = str(e)[:200]
    # One title format for every chapter (owner, 2026-10-05): the model writes
    # the hook, the code builds the title with a stable series name.
    try:
        card["title_template"] = _studio.title_template()
        card["title_series"] = ((card.get("web_facts") or {}).get("english_title")
                                or _cs.clean_title(card.get("series") or "", card.get("source_url") or "")
                                or card.get("series") or "")
    except Exception:
        pass
    _seo_stage(pdir, name, "your channel's uploads + YouTube searches")
    style, res = {"titles": {"samples": 0}, "descriptions": {"samples": 0,
                                                             "top_hashtags": []}}, {}
    quota = 0
    if yt_api.configured():
        client = yt_api.Client()
        style = _seo.channel_style(client, _yt_root(), force=body.refresh_style)
        res = _seo.research(client, card)             # 3. bounded research
        quota = client.spent
    else:
        res = {"ok": False, "error": "no YouTube API key configured",
               "n": 0, "patterns": {}, "top": []}

    _seo_stage(pdir, name, "writing the title, description and tags")
    try:
        out = _seo.generate(pdir, name, card, style, res, usage=usage)
    except usage.UsageCapExceeded:
        raise
    except Exception as e:
        raise HTTPException(502, "SEO generation failed: %s" % str(e)[:300])

    try:
        import seo_research as _sw2
        credit = _sw2.credit_line((web or {}).get("facts"))
        if credit and credit not in (out.get("description") or ""):
            out["description"] = ((out.get("description") or "").rstrip() + "\n\n" + credit)[:YT_DESC_MAX]
    except Exception:
        pass
    conf = _seo.confidence(card, style, res)
    prev = _seo.get(pdir, name) or {}
    rec = {
        **out,
        "confidence": conf,
        "detected_from": {
            "source_url": card.get("source_url"),
            "series": card.get("series"), "chapter": card.get("chapter"),
            "aliases": card.get("aliases"), "genre": card.get("genre"),
            "characters": card.get("characters"),
            "n_segments": card.get("n_segments"), "n_panels": card.get("n_panels"),
            "narration_chars": card.get("narration_chars"),
            "gaps": card.get("gaps"),
        },
        "sources": {
            "project": {"label": "Ingested chapter", "url": card.get("source_url"),
                        "detail": "project.json + narration + %d panel descriptions"
                                  % card.get("n_panels", 0)},
            "channel": {"label": (style.get("channel_title") or "channel style"),
                        "url": "https://www.youtube.com/" + _seo.CHANNEL_HANDLE,
                        "detail": "%d uploads measured" % (style.get("titles", {})
                                                           .get("samples", 0)),
                        "error": style.get("error")},
            "research": {"label": "YouTube search", "query": res.get("query"),
                         "detail": "%d comparable videos" % res.get("n", 0),
                         "top": res.get("top", []), "error": res.get("error")},
            "series_youtube": {"label": "YouTube search for this series",
                               "query": (res.get("series") or {}).get("query"),
                               "detail": "%d videos" % (res.get("series") or {}).get("n", 0),
                               "top": (res.get("series") or {}).get("top", []),
                               "error": (res.get("series") or {}).get("error")},
            "web": {"label": "Web research (Google)",
                    "detail": ("%d sources%s" % (len((web or {}).get("sources") or []),
                                                  " · saved research" if (web or {}).get("from_cache") else ""))
                              if web else "none",
                    "facts": (web or {}).get("facts") or {},
                    "queries": (web or {}).get("queries") or [],
                    "sources": [{"domain": x.get("domain"), "url": x.get("url"), "tier": x.get("tier")}
                                for x in ((web or {}).get("sources") or [])[:10]],
                    "error": web_err or (web or {}).get("refresh_error")},
        },
        "style_signal": style.get("titles", {}),
        "cut_signature": cut_signature(pdir=pdir),
        "generated_at": time.time(),
        "quota_units": quota,
        # Applying is the operator's action; regenerating must not forget what
        # they already accepted.
        "applied": prev.get("applied", {}),
    }
    _seo.put(pdir, name, rec)
    return _seo_state(pdir, name)


_SEO_RUNS = {}          # (project dir, export) -> live progress of a 🔎 SEO run


def _seo_stage(pdir, name, stage):
    r = _SEO_RUNS.get((pdir, name))
    if r is not None and r.get("status") == "running":
        if r.get("stage"):
            r["steps"].append({"step": r["stage"], "secs": round(time.time() - r["stage_at"], 1)})
        r["stage"], r["stage_at"] = stage, time.time()


@app.post("/api/seo/run/start")
def api_seo_run_start(body: SeoGenIn):
    """Start a 🔎 SEO run in the background and return at once; the page polls
    /api/seo/run/status, which shows each step and how long it took (owner,
    2026-10-06: "how long does it take and how do I know it's working")."""
    import threading
    pdir = project_dir_for(body.project)
    name = os.path.basename(body.name or "")
    if not name:
        raise HTTPException(400, "which video is this for?")
    key = (pdir, name)
    if (_SEO_RUNS.get(key) or {}).get("status") == "running":
        return {"ok": True, "already": True, **_seo_run_view(key)}
    _SEO_RUNS[key] = {"status": "running", "started": time.time(), "stage": None, "stage_at": time.time(),
                      "steps": [], "result": None, "error": None}

    def go():
        r = _SEO_RUNS[key]
        try:
            res = api_seo_run(body)
            _seo_stage(pdir, name, None)
            r.update(status="done", result={k: res.get(k) for k in ("filled", "failed", "ran", "problems", "metadata")})
        except usage.UsageCapExceeded as e:
            r.update(status="error", error="today's spend limit is used up — " + str(e)[:160])
        except HTTPException as e:
            r.update(status="error", error=str(e.detail)[:300])
        except Exception as e:  # noqa
            r.update(status="error", error=str(e)[:300])
        r["ended"] = time.time()
    threading.Thread(target=go, daemon=True).start()
    return {"ok": True, **_seo_run_view(key)}


def _seo_run_view(key):
    r = _SEO_RUNS.get(key)
    if not r:
        return {"status": "none"}
    end = r.get("ended") or time.time()
    return {"status": r["status"], "stage": r.get("stage"), "elapsed": round(end - r["started"], 1),
            "stage_secs": round(time.time() - r["stage_at"], 1) if r["status"] == "running" else None,
            "steps": r["steps"], "result": r.get("result"), "error": r.get("error")}


@app.get("/api/seo/run/status")
def api_seo_run_status(project: str, name: str):
    return _seo_run_view((project_dir_for(project), os.path.basename(name)))


# ====================================================================
#  Spec 10 — per-chapter SEO system (docs/audit/10_SEO_SYSTEM_SPEC.md)
#  The title is series-locked ("[N] hook — series | Manhwa Recap"); the
#  description and tags do the literal matching. chapter_seo.py builds it,
#  series_hooks.py is the one creative decision per series (Phase D).
# ====================================================================
SEO_CHAPTER_NAME = "seo_chapter.json"


def _went_out(pdir, name):
    """True when this export was already posted (Upload-Post or direct upload)."""
    rec = (load_publishes(pdir) or {}).get(name) or {}
    if any(r.get("status") == "published" for r in rec.get("results") or []):
        return True
    return ((_read_json(os.path.join(pdir, UPLOADS_NAME)) or {}).get(name) or {}).get("status") == "uploaded"


def _seo_chapter_rec(pdir):
    return _read_json(os.path.join(pdir, SEO_CHAPTER_NAME)) or {}


def _seo_chapter_save(pdir, rec):
    tmp = os.path.join(pdir, SEO_CHAPTER_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
    os.replace(tmp, os.path.join(pdir, SEO_CHAPTER_NAME))


def _chapter_page_title(url, timeout=15):
    """Stage 1 (best effort): the chapter's own title from its release page
    ("Chapter 45 - Intensifying by the Minute"). '' when the page has none."""
    import urllib.request
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read(400000).decode("utf-8", "ignore")
    except Exception:
        return ""
    for pat in (r'property="og:title"\s+content="([^"]+)"', r"<title>([^<]+)</title>", r"<h1[^>]*>([^<]+)</h1>"):
        m = re.search(pat, html, re.I)
        if not m:
            continue
        t = __import__("html").unescape(m.group(1))
        mm = re.search(r"(?i)chapter\s*[\d.]+\s*[-–—:|]\s*(.+?)(\s*[-–—|]\s*(asura|read|manhwa|webtoon).*)?$", t)
        if mm:
            cand = mm.group(1).strip(" -–—:|")
            if cand and not re.search(r"(?i)asura|scans|read online|^chapter", cand) and len(cand) <= 80:
                return cand
    return ""


def _chapter_tease(pdir, api_key):
    """Stage 2: from the approved script — the dramatic beat, a NON-SPOILER
    tease (≤ 180 chars) and 1-2 new names. One metered call, cached by the
    script's hash, so an unchanged script is never paid for twice."""
    import hashlib
    import series_research as sr
    try:
        with open(os.path.join(pdir, "script.txt"), encoding="utf-8") as f:
            script = f.read()
    except OSError:
        return {}
    h = hashlib.sha1(script.encode()).hexdigest()[:16]
    rec = _seo_chapter_rec(pdir)
    if (rec.get("hook_beat") or {}).get("script_hash") == h:
        return rec["hook_beat"]
    if not api_key:
        return {}
    meta = _read_json(os.path.join(pdir, "project.json")) or {}
    resp = sr._post({"contents": [{"parts": [{"text": (
        "This is the narration script of one manhwa chapter recap. Return ONLY JSON "
        '{"dramatic_beat": "the chapter\'s biggest moment in one short sentence", '
        '"tease": "ONE or TWO sentences, max 180 characters, that make a viewer want to watch WITHOUT revealing how '
        'the chapter ends; no chapter numbers, no counts of chapters", '
        '"new_entities": ["1-2 character or place names that matter in this chapter"]}\n'
        f"Series: {meta.get('series')}, chapter {meta.get('chapter')}.\n\nSCRIPT:\n{script[:9000]}")}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1024, "responseMimeType": "application/json"}},
        api_key)
    try:
        out = json.loads(sr._text(resp))
    except Exception:
        out = {}
    beat = {"dramatic_beat": str(out.get("dramatic_beat") or "")[:200],
            "tease": " ".join(str(out.get("tease") or "").split())[:200],
            "new_entities": [str(x)[:40] for x in (out.get("new_entities") or [])][:2],
            "source": "approved script", "script_hash": h, "at": time.time()}
    rec = _seo_chapter_rec(pdir)
    rec["hook_beat"] = beat
    _seo_chapter_save(pdir, rec)
    return beat


def _competitor_check(series, n):
    """Stage 3: a fresh YouTube search for '<series> Chapter N' (100 quota
    units). Records whether the exact phrase is already taken; informational."""
    import yt_api
    if not yt_api.configured():
        return {"checked_at": time.time(), "error": "no YouTube API key"}
    q = f"{series} Chapter {n}"
    try:
        hits = yt_api.Client().search_recaps(q, limit=10)
    except Exception as e:
        return {"checked_at": time.time(), "error": str(e)[:200]}
    taken = [h for h in hits if q.lower() in (h.get("title") or "").lower()]
    return {"checked_at": time.time(), "query": q, "exact_phrase_taken": bool(taken),
            "competitors": [{"title": h["title"], "channel": h["channel"]} for h in hits[:5]]}


def _music_credit_for(pdir):
    """C2: one line, only when this chapter's render used background music."""
    credit = (_studio.load().get("music_credit") or "").strip()
    used = os.path.exists(os.path.join(pdir, "bgm.mp3")) or bool(os.environ.get("HF_BGM"))
    return credit if (credit and used) else ""


def _series_locked(pdir):
    """(pack, title_lock) when this chapter's series has an approved hook."""
    # rebuilt each time (local cached sources only — free), so a fix to how the
    # series fields are derived reaches every package; owner keys survive
    pack = _series_pack(pdir) or {}
    lock = pack.get("title_lock") or {}
    return pack, (lock if lock.get("approved_by_user") and lock.get("series_hook") else None)


def _build_chapter_package(pdir, name, api_key=None, competitor=True, lock_override=None, save=True):
    """Stages 1-3 for one chapter export -> {title, description, tags, hashtags,
    problems, ...}, saved to seo_chapter.json. Raises ValueError when the
    series has no locked hook yet (Phase D comes first)."""
    import chapter_seo as _cseo
    pack, lock = _series_locked(pdir)
    if lock_override:
        lock = lock_override
    if not lock:
        raise ValueError("this series has no title hook yet — pick one in Library → the series → 🏷 Title hook")
    sid, series_raw, n, meta = _series_ident(pdir)
    if n is None:
        raise ValueError("this video has no chapter number")
    series = pack.get("series_name_en") or series_raw
    rec = _seo_chapter_rec(pdir)
    if "chapter_title" not in rec:                                   # Stage 1 (once)
        rec["chapter_title"] = _chapter_page_title(meta.get("url") or "")
        rec["chapter_title_provenance"] = meta.get("url") or ""
        _seo_chapter_save(pdir, rec)
    beat = _chapter_tease(pdir, api_key) if api_key else (rec.get("hook_beat") or {})   # Stage 2
    prev_dir = _chapter_project(sid, n - 1)
    pl = pack.get("playlist_id") or ""
    # only a real playlist id (live 2026-10-08: series defaults had stored the
    # playlist NAME there, which made "list=The Regressed Mercenary …")
    if not re.fullmatch(r"(PL|UU|OL|FL)[\w-]{10,}", pl):
        pl = ""
    pl_link = f"https://www.youtube.com/playlist?list={pl}" if pl else ""
    prev_link = _published_video_url(prev_dir)
    desc = _cseo.build_description(series, n, pack, chapter_title=rec.get("chapter_title") or "",
                                   tease=beat.get("tease") or "", prev_n=n - 1, prev_link=prev_link,
                                   playlist_link=pl_link, timecodes=[], music_credit=_music_credit_for(pdir))
    pkg = {"title": _cseo.build_title(n, lock, series), "description": desc,
           "tags": _cseo.build_tags(series, n, pack), "hashtags": _cseo.hashtags(series),
           "first_comment": " | ".join(x for x in ([f"Start from Chapter 1: {pl_link}"] if pl_link else [])
                                       + ([f"Chapter {n - 1}: {prev_link}"] if prev_link else [])),
           "playlist_id": pl}
    pkg["problems"] = _cseo.validate(pkg, series, n)
    if competitor:
        pkg["competitor_check"] = _competitor_check(series, n)              # Stage 3
    if save:
        rec = _seo_chapter_rec(pdir)
        rec.setdefault("exports", {})[name] = {**pkg, "built_at": time.time(), "status": "awaiting_approval"}
        _seo_chapter_save(pdir, rec)
    return pkg


def _apply_chapter_package(pdir, name, pkg):
    store = load_publish(pdir)
    md = {**publish_defaults(pdir), **(store.get(name) or {})}
    md.update(title=pkg["title"], description=pkg["description"], tags=list(pkg["tags"]),
              seo_v2=True, youtube_playlist_id=pkg.get("playlist_id") or "",
              first_comment=pkg.get("first_comment") or "",
              summary_block=(_seo_chapter_rec(pdir).get("hook_beat") or {}).get("tease") or md.get("summary_block"),
              summary_at=time.time())
    store[name] = md
    save_publish(pdir, store)
    return md


class HookGenIn(BaseModel):
    project: str


class SeoPreviewIn(BaseModel):
    project: str
    name: str = ""
    hooks: list[str] = []              # empty: the locked hook, else the series' candidates


@app.post("/api/series/seo/preview")
def api_series_seo_preview(body: SeoPreviewIn):
    """What spec 10 WOULD publish for this video, per hook — nothing is
    locked, applied or changed on the video (owner, 2026-10-08: 'run it on
    Regressed Mercenary ch.96 — it's queued, I want to see the results').
    Costs: the teaser call (cached by script) + one YouTube search."""
    import gemini_tts as _gt
    pdir = project_dir_for(body.project)
    name = os.path.basename(body.name or "") or (latest_export(body.project)[0] or "")
    pack, lock = _series_locked(pdir)
    hooks = [h for h in body.hooks if h.strip()] or ([lock["series_hook"]] if lock else
                                                      [c["hook"] for c in (pack.get("hook_candidates") or {}).get("candidates") or []
                                                       if not c.get("problems")])
    if not hooks:
        raise HTTPException(409, "no hook to preview — suggest hooks for the series first")
    key = _gt.env_any_case("GEMINI_API_KEY")
    import series_hooks as _sh
    sid, series_raw, n, _m = _series_ident(pdir)
    series = pack.get("series_name_en") or series_raw
    out = []
    for i, h in enumerate(hooks[:3]):
        try:
            lk = _sh.lock(series, h)
        except ValueError as e:
            out.append({"hook": h, "error": str(e)})
            continue
        pkg = _build_chapter_package(pdir, name, key, competitor=(i == 0), lock_override=lk, save=False)
        out.append({"hook": h, **pkg})
    cur = {**publish_defaults(pdir), **(load_publish(pdir).get(name) or {})}
    return {"project": os.path.basename(pdir.rstrip("/")), "name": name, "previews": out,
            "current": {k: cur.get(k) for k in ("title", "description", "tags")},
            "chapter_title": _seo_chapter_rec(pdir).get("chapter_title"),
            "hook_beat": _seo_chapter_rec(pdir).get("hook_beat")}


class HookLockIn(BaseModel):
    project: str
    hook: str
    mechanic: int | None = None
    provenance: list[str] = []
    apply: bool = True                 # rebuild this series' unposted chapters with it


class PlaylistIn(BaseModel):
    project: str
    playlist: str                      # a playlist URL or its id


@app.get("/api/series/seo")
def api_series_seo(project: str):
    """The series' spec-10 record: locked hook, candidates, playlist, the
    fields the description/tags use, and an example title."""
    import chapter_seo as _cseo
    pdir = project_dir_for(project)
    pack = _series_pack(pdir) or {}
    series = pack.get("series_name_en") or ""
    lock = pack.get("title_lock")
    return {"series": series, "title_lock": lock, "hook_candidates": pack.get("hook_candidates"),
            "playlist_id": pack.get("playlist_id") or "", "hook_budget": _cseo.hook_budget(series, 99),
            "example_title": _cseo.build_title(45, lock, series) if lock else None,
            "fields": {k: pack.get(k) for k in ("series_name_alt", "series_name_ko", "series_hashtag",
                                                "authors", "publisher", "genres", "characters_main")},
            "tags_series_block": _cseo.series_block(pack)}


@app.post("/api/series/hooks")
def api_series_hooks(body: HookGenIn):
    """Phase D: three hook candidates from the series' cached sources (one
    metered model call). Nothing is locked until the owner picks."""
    import series_hooks as _sh
    import gemini_tts as _gt
    pdir = project_dir_for(body.project)
    sid, series_raw, _n, meta = _series_ident(pdir)
    pack = _series_pack(pdir) or {}
    series = pack.get("series_name_en") or series_raw
    bible = None
    try:
        import series_bible as _sbib
        bible = _sbib.load_series_bible(sid, pdir)
    except Exception:
        pass
    try:
        import seo_research as _sw
        # Phase B: the web research (official/Korean titles, authors, publisher,
        # genres) runs first when this series has none yet — cached a week.
        web = _sw.get(_yt_root(), series, list(pack.get("aliases") or []), meta.get("url") or "",
                      _gt.env_any_case("GEMINI_API_KEY"))
        if web and (web.get("facts") or {}):
            pack = _series_pack(pdir) or pack
    except usage.UsageCapExceeded as e:
        raise HTTPException(409, "today's spend limit is used up — " + str(e)[:120])
    except Exception:
        web = None
    try:
        res = _sh.generate(series, pack, bible, web, _gt.env_any_case("GEMINI_API_KEY"))
    except ValueError as e:
        raise HTTPException(409, str(e))
    import series_pack as _sp
    pack["hook_candidates"] = res
    _sp.save(_yt_root(), pack)
    _ev("publish", f"{series}: 3 title-hook ideas ready — pick one in Library", "ok")
    return res


@app.post("/api/series/hooks/lock")
def api_series_hooks_lock(body: HookLockIn):
    """The owner's choice becomes the series' title_lock (T1/T3/T4). With
    apply, every chapter of the series that hasn't posted gets its package."""
    import series_hooks as _sh
    import series_pack as _sp
    pdir = project_dir_for(body.project)
    sid, series_raw, _n, _m = _series_ident(pdir)
    pack = _series_pack(pdir) or {}
    series = pack.get("series_name_en") or series_raw
    try:
        lock = _sh.lock(series, body.hook, body.mechanic, body.provenance)
    except ValueError as e:
        raise HTTPException(400, f"that hook can't be used: {e}")
    pack["title_lock"] = lock
    _sp.save(_yt_root(), pack)
    applied = []
    if body.apply:
        applied = _apply_series_packages(sid)
    _ev("publish", f"{series}: title hook locked — \"{lock['series_hook']}\" ({len(applied)} chapter(s) updated)", "ok")
    return {"ok": True, "title_lock": lock, "applied": applied}


def _apply_series_packages(sid, api_key=None):
    """Rebuild + apply the package on every export of the series that hasn't
    posted (local only unless a key is passed; no competitor search)."""
    import ingest as _i
    out = []
    for pid in sorted(os.listdir(_i.PROJECTS)):
        pdir = os.path.join(_i.PROJECTS, pid)
        if pid.startswith("_") or not os.path.isdir(os.path.join(pdir, "exports")):
            continue
        if _series_ident(pdir)[0] != sid:
            continue
        for name in sorted(os.listdir(os.path.join(pdir, "exports"))):
            if not name.endswith(".mp4") or _went_out(pdir, name):
                continue
            try:
                _apply_chapter_package(pdir, name, _build_chapter_package(pdir, name, api_key, competitor=False))
                out.append(pid)
            except Exception as e:  # noqa
                print(f"[seo-v2] {pid}/{name}: {str(e)[:160]}", flush=True)
    return out


@app.post("/api/series/playlist")
def api_series_playlist(body: PlaylistIn):
    """Phase E/P2: the series playlist (created on YouTube by the owner —
    the deployment has no YouTube write access); videos are added to it by
    Upload-Post's youtube_playlist_id when they post."""
    import series_pack as _sp
    m = re.search(r"(?:list=)?(PL[\w-]{10,}|UU[\w-]{10,}|OL[\w-]{10,})", body.playlist or "")
    if not m:
        raise HTTPException(400, "paste the playlist's link (it contains list=PL…)")
    pdir = project_dir_for(body.project)
    pack = _series_pack(pdir) or {}
    pack["playlist_id"] = m.group(1)
    _sp.save(_yt_root(), pack)
    applied = _apply_series_packages(_series_ident(pdir)[0]) if (pack.get("title_lock") or {}).get("approved_by_user") else []
    return {"ok": True, "playlist_id": m.group(1), "applied": applied}


@app.post("/api/seo/run")
def api_seo_run(body: SeoGenIn):
    """The one-click SEO (owner, 2026-10-06: "a button called SEO … to get real
    and live seo"). Fresh research — the web (Google) research and the
    channel's latest uploads are re-fetched, the YouTube searches run live —
    then the title, description and tags are FILLED IN from it in the current
    formats (B1 title, B2 description blocks, B4 aliases in the tags).
    Replaces those three fields; everything is metered by usage.gate."""
    import seo as _seo
    pdir = project_dir_for(body.project)
    name = os.path.basename(body.name or "")
    if not name:
        raise HTTPException(400, "which video is this for?")
    if _series_locked(pdir)[1]:
        # Spec 10: the series' locked title + the chapter's package (tease from
        # the script, fresh competitor check), not a free-form SEO title.
        import gemini_tts as _gt
        _seo_stage(pdir, name, "chapter title, tease from the script, YouTube check")
        pkg = _build_chapter_package(pdir, name, _gt.env_any_case("GEMINI_API_KEY"))
        _seo_stage(pdir, name, "filling in what gets published")
        md = _apply_chapter_package(pdir, name, pkg)
        pid = os.path.basename(pdir.rstrip("/"))
        cc = pkg.get("competitor_check") or {}
        return {"ok": True, "filled": ["title", "description", "tags"], "failed": {},
                "ran": {"series_lock": {"label": "Series title hook", "detail": "locked", "error": None},
                        "competitor": {"label": "YouTube check for this chapter",
                                       "detail": ("exact phrase already used by another channel"
                                                  if cc.get("exact_phrase_taken") else "exact phrase free"),
                                       "error": cc.get("error")}},
                **_publish_payload(pdir, pid, name, md)}
    api_seo_generate(SeoGenIn(project=body.project, name=name, refresh_style=True))
    _seo_stage(pdir, name, "filling in what gets published")
    filled, failed = [], {}
    for field in ("title", "description", "tags"):
        try:
            api_seo_apply(SeoApplyIn(project=body.project, name=name, field=field))
            filled.append(field)
        except HTTPException as e:
            failed[field] = str(e.detail)[:160]
    rec = _seo.get(pdir, name) or {}
    src = rec.get("sources") or {}

    def line(k):
        x = src.get(k) or {}
        return {"label": x.get("label"), "detail": x.get("detail"), "error": x.get("error")}
    pid = os.path.basename(pdir.rstrip("/"))
    md = {**publish_defaults(pdir), **(load_publish(pdir).get(name) or {})}
    _ev("publish", f"{(_project_label(pdir) or {}).get('title') or pid}: SEO researched and filled in "
                   f"({', '.join(filled) or 'nothing'})", "ok" if filled else "warn")
    return {"ok": bool(filled), "filled": filled, "failed": failed,
            "ran": {k: line(k) for k in ("web", "channel", "research", "series_youtube")},
            "confidence": rec.get("confidence"), **_publish_payload(pdir, pid, name, md)}


def _all_series_with_chapters():
    """Returns sorted list of (series_id, pdir) for all distinct series with projects."""
    import ingest as _i
    seen = {}
    if not os.path.isdir(_i.PROJECTS):
        return []
    for pid in sorted(os.listdir(_i.PROJECTS)):
        if pid.startswith("_"):
            continue
        pdir = os.path.join(_i.PROJECTS, pid)
        if not os.path.isdir(pdir):
            continue
        try:
            sid, sname, ch_n, meta = _series_ident(pdir)
            if sid and sid not in seen:
                seen[sid] = pdir
        except Exception:
            continue
    return sorted(seen.items())


def _bible(series_id, pdir=None):
    try:
        import series_bible as _sbib
        return _sbib.load_series_bible(series_id, pdir=pdir)
    except Exception:
        return None


@app.post("/api/seo/batch_setup")
def api_seo_batch_setup():
    """Spec-10 Phase B+D for EVERY series that has chapters, with skip rules."""
    import series_research as sr
    import series_hooks as sh
    import series_pack as sp
    import gemini_tts as _gt
    import ingest as _i
    key = _gt.env_any_case("GEMINI_API_KEY")
    results = []
    for series_id, pdir in _all_series_with_chapters():          # existing project dirs
        pack = sp.load(_i.PROJECTS, series_id) or _series_pack(pdir) or {}
        lock = (pack or {}).get("title_lock") or {}
        # --- SKIP RULES (idempotency) ---
        if lock.get("approved_by_user"):
            results.append({"series": series_id, "action": "skipped: hook already locked"})
            continue
        if (pack or {}).get("playlist_id") and (lock.get("hook_candidates") or (pack or {}).get("hook_candidates")):
            results.append({"series": series_id, "action": "skipped: already set up, awaiting owner pick"})
            continue
        # --- RESEARCH (cached: only if none / older than 7 days) ---
        r = (pack or {}).get("seo_research") or {}
        if not r or (time.time() - r.get("at", 0)) > 7 * 86400:
            try:
                r = sr.research_series_seo(series_id, pack, api_key=key)      # the two prompts from §1
                pack["seo_research"] = r
            except Exception as e:
                results.append({"series": series_id, "action": "error: research", "error": str(e)[:200]})
                _ev("seo_batch", f"{series_id}: research failed — {e}", "warn")
                continue
        # --- HOOK CANDIDATES (generate; NEVER lock) ---
        try:
            series_name = pack.get("series_name_en") or pack.get("title_en") or series_id
            cands = sh.generate(series_name, pack, _bible(series_id, pdir), r, key)
            pack["hook_candidates"] = cands
            sp.save(_i.PROJECTS, pack)   # OWNER_KEYS survive — 43c1439 fix
            cand_titles = [c["example_title"] for c in cands.get("candidates", [])]
            results.append({"series": series_id, "action": "hooks ready — awaiting owner pick",
                            "candidates": cand_titles})
            _ev("seo_batch", f"{series_id}: 3 hook candidates ready", "ok")
        except Exception as e:
            results.append({"series": series_id, "action": "error: hooks", "error": str(e)[:200]})
            _ev("seo_batch", f"{series_id}: hook generation failed — {e}", "warn")
    return {"ok": True, "results": results}


# ====================================================================
#  Spec 06 B2/B4 — series asset pack + fixed description blocks
# ====================================================================
def _series_ident(pdir):
    """(series_id, series name, chapter number as int or None, meta)."""
    import ingest as _ing
    meta = _read_json(os.path.join(pdir, "project.json")) or {}
    slug = ""
    try:
        slug, _c = _ing.parse_series_chapter(meta.get("url") or "")
        slug = _ing.clean_series_slug(slug)
    except Exception:
        slug = ""
    series = (meta.get("series") or "").strip()
    sid = slug or re.sub(r"[^a-z0-9]+", "-", series.lower()).strip("-")
    try:
        n = int(float(str(meta.get("chapter") or "").strip()))
    except ValueError:
        n = None
    return sid, series, n, meta


def _series_pack(pdir, refresh=True):
    """The B4 pack for this chapter's series, refreshed from every local
    source (no network, no model: web research is read from its cache)."""
    import series_pack as _sp
    sid, series, n, meta = _series_ident(pdir)
    if not sid:
        return None
    root = _yt_root()
    prev = _sp.load(root, sid)
    if prev and not refresh:
        return prev
    wf, bible, watch, style, total = {}, None, None, None, None
    try:
        import seo_research as _sw
        web = _sw.get(root, series, [], meta.get("url") or "", None)
        wf = (web or {}).get("facts") or {}
    except Exception:
        wf = {}
    try:
        import series_bible as _sbib
        bible = _sbib.load_series_bible(sid, pdir)
    except Exception:
        bible = None
    try:
        import watchlist as _wl
        watch, m = _wl.find_by_mirror(_wl.load(_wl_root()), meta.get("url") or "")
        lat = [float(x.get("latest")) for x in (watch or {}).get("mirrors") or [] if x.get("latest") is not None]
        total = int(max(lat)) if lat else None
    except Exception:
        watch = None
    try:
        import thumbnail_studio as _ts
        style = _ts.load_style(root, _ts.series_key(meta))
    except Exception:
        style = None
    try:
        import seo as _seo
        slug_al = _seo._aliases(series, meta.get("url") or "", None)
    except Exception:
        slug_al = []
    pack = _sp.build(sid, series=series, web_facts=wf, bible=bible, watch=watch, slug_aliases=slug_al,
                     source_url=meta.get("url") or "", total_chapters=total, style=style, prev=prev)
    return _sp.save(root, pack)


def _chapter_project(sid, n):
    import ingest as _ing
    if not sid or n is None:
        return None
    d = os.path.join(_ing.PROJECTS, f"{sid}_{n}")
    return d if os.path.isdir(d) else None


def _published_video_url(pdir):
    """The chapter's published YouTube link, if it has one."""
    if not pdir:
        return ""
    try:
        pubs = load_publishes(pdir)
    except Exception:
        pubs = {}
    for rec in pubs.values():
        for r in (rec or {}).get("results") or []:
            if r.get("status") == "published" and (r.get("network") or "youtube") == "youtube":
                u = _post_url({"post_url": r.get("url") or r.get("post_url"),
                               "platform_post_id": r.get("platform_post_id")}, "youtube")
                if u:
                    return u
    for rec in (_read_json(os.path.join(pdir, UPLOADS_NAME)) or {}).values():
        vid = (rec or {}).get("video_id") if isinstance(rec, dict) else None
        if vid:
            return f"https://youtu.be/{vid}"
    return ""


def _prev_summary_block(pdir, name=None):
    """The previous chapter's block 2 (its saved summary_block), or ''."""
    sid, _s, n, _m = _series_ident(pdir)
    prev = _chapter_project(sid, n - 1) if n else None
    if not prev:
        return ""
    store = load_publish(prev)
    for k, md in sorted(store.items(), key=lambda kv: -(kv[1] or {}).get("summary_at", 0)
                        if isinstance(kv[1], dict) else 0):
        if k != DRAFT and isinstance(md, dict) and md.get("summary_block"):
            return md["summary_block"]
    return ""


def _description_from_blocks(pdir, md, rec, variant=""):
    """B2: the description in the fixed block order, built on the SEO record.
    Returns (text, summary_block)."""
    import chapter_title as _ct
    import description_blocks as _db
    import series_pack as _sp
    sid, series, n, meta = _series_ident(pdir)
    pack = _series_pack(pdir) or {}
    hook = _ct.hook_part(md.get("title") or "", series, n) or next(
        (t.get("hook") for t in rec.get("titles") or [] if t.get("recommended") and t.get("hook")), "")
    summ = (rec.get("summary") or "") if variant != "short" else ""
    if not summ:
        summ = _db.summary_from(rec.get("description_short" if variant == "short" else "description") or "")
    pl = pack.get("playlist_id") or ""
    text = _db.build(hook=hook, series=pack.get("title_en") or series, chapter=n if n is not None else "",
                     summary=summ, pack=pack,
                     playlist_url=f"https://www.youtube.com/playlist?list={pl}" if pl else "",
                     prev_url=_published_video_url(_chapter_project(sid, n - 1) if n else None),
                     next_url=_published_video_url(_chapter_project(sid, n + 1) if n is not None else None),
                     footer=_studio.load().get("description_footer") or "",
                     hashtags=rec.get("hashtags") or [], limit=YT_DESC_MAX)
    return text, _db.summary_from(summ)


# ====================================================================
#  Spec 06 B3 — range compilations (Track B). range_compile.py has the rules.
# ====================================================================
def _approved_export(pdir):
    """A chapter's latest export that is approved and current, or None."""
    d = os.path.join(pdir, "exports")
    try:
        names = sorted((n for n in os.listdir(d) if n.endswith(".mp4")),
                       key=lambda n: -os.path.getmtime(os.path.join(d, n)))
    except OSError:
        return None
    for n in names:
        rv = review_state(pdir, n)
        if rv["status"] == "approved" and not rv["superseded"]:
            return n
    return None


class RangeIn(BaseModel):
    project: str          # any chapter of the series
    chapters_start: int
    chapters_end: int


def _range_ctx(body):
    import ingest as _ing
    import range_compile as _rc
    pdir = project_dir_for(body.project)
    sid, series, _n, meta = _series_ident(pdir)
    if not sid:
        raise HTTPException(400, "that project has no series")
    pack = _series_pack(pdir) or {}
    pl = _rc.plan(_ing.PROJECTS, sid, int(body.chapters_start), int(body.chapters_end), _approved_export)
    return pdir, sid, (pack.get("title_en") or series), pack, pl


@app.post("/api/ranges/plan")
def api_range_plan(body: RangeIn):
    """What a range would contain and be called — nothing is built."""
    import range_compile as _rc
    try:
        _p, sid, series, pack, pl = _range_ctx(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    secs = 0.0
    for n, cp, name in pl["chapters"]:
        try:
            secs += _rc._probe(os.path.join(cp, "exports", name))
        except Exception:
            pass
    genre = (pack.get("genre") or [""])[0]
    t, dropped = _rc.title(series, body.chapters_start, body.chapters_end, secs, genre)
    return {"series_id": sid, "chapters_start": int(body.chapters_start), "chapters_end": int(body.chapters_end),
            "chapters": [n for n, _c, _x in pl["chapters"]], "missing": pl["missing"],
            "title": t, "title_chars": len(t), "dropped": dropped, "seconds": round(secs, 1),
            "ready": not pl["missing"]}


@app.post("/api/ranges/build")
def api_range_build(body: RangeIn):
    """Stitch chapters start..end (each chapter's latest approved export, stream
    copy — no model, no paid call) into a NEW range project, then prefill its
    title (built from the two stored integers) and description. Refuses when
    any chapter in the range has no approved export."""
    import range_compile as _rc
    import ingest as _ing
    import threading
    try:
        src, sid, series, pack, pl = _range_ctx(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if pl["missing"]:
        raise HTTPException(409, "no approved video yet for chapter(s) " +
                            ", ".join(str(n) for n in pl["missing"][:30]))
    a, b = int(body.chapters_start), int(body.chapters_end)
    rdir, meta = _rc.write_project(_ing.PROJECTS, sid, series, a, b,
                                   url=_read_json(os.path.join(src, "project.json")).get("url") or "")
    name = f"final_range_{a}-{b}.mp4"

    def run():
        try:
            marks = _rc.stitch(pl["chapters"], os.path.join(rdir, "exports", name))
            secs = _rc._probe(os.path.join(rdir, "exports", name))
            t, _d = _rc.title(series, a, b, secs, (pack.get("genre") or [""])[0])
            import description_blocks as _db
            desc = _db.build(hook=f"Full recap of {series} chapters {a} to {b}", series=series,
                             chapter=f"{a}-{b}", summary="", pack=pack, arcs=_rc.arcs(marks),
                             footer=_studio.load().get("description_footer") or "",
                             hashtags=["#manhwa", "#manhwarecap"], limit=YT_DESC_MAX)
            store = load_publish(rdir)
            store[name] = {**publish_defaults(rdir), "title": t, "description": desc,
                           "playlist": series}
            save_publish(rdir, store)
            m = _read_json(os.path.join(rdir, "project.json"))
            m["build"] = {"status": "done", "at": time.time(), "export": name, "marks": marks}
            json.dump(m, open(os.path.join(rdir, "project.json"), "w"), indent=2)
            _ev("publish", f"{series} chapters {a}-{b}: range video built — review it before it can post", "ok")
        except Exception as e:  # noqa
            m = _read_json(os.path.join(rdir, "project.json"))
            m["build"] = {"status": "error", "at": time.time(), "error": str(e)[:300]}
            json.dump(m, open(os.path.join(rdir, "project.json"), "w"), indent=2)
            _ev("publish", f"{series} chapters {a}-{b}: range build failed — {str(e)[:160]}", "warn")

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "project": os.path.basename(rdir), "export": name,
            "chapters_start": a, "chapters_end": b}


@app.get("/api/ranges")
def api_ranges(project: str = ""):
    """Range videos of this chapter's series (or all series)."""
    import ingest as _ing
    sid = _series_ident(project_dir_for(project))[0] if project else ""
    out = []
    for m in _ing.list_projects(include_ranges=True):
        if m.get("kind") == "range" and (not sid or m.get("series_id") == sid):
            out.append({k: m.get(k) for k in ("id", "series", "series_id", "chapters_start",
                                              "chapters_end", "build", "created_at")})
    return {"ranges": sorted(out, key=lambda r: (r["series_id"] or "", r["chapters_start"] or 0))}


@app.post("/api/seo/apply")
def api_seo_apply(body: SeoApplyIn):
    """Copy ONE suggestion into the real publish metadata.

    The publish record stays the source of truth: this is the only path by
    which a suggestion reaches it. It runs on a click, or from
    _prepare_publish for fields still at their automatic default (2026-10-05).
    """
    import seo as _seo
    pdir = project_dir_for(body.project)
    name = os.path.basename(body.name or "")
    rec = _seo.get(pdir, name) or _seo.get(pdir, DRAFT)
    if not rec:
        raise HTTPException(404, "no SEO suggestions for that export yet")

    store = load_publish(pdir)
    md = {**publish_defaults(pdir), **(store.get(name) or {})}
    field = (body.field or "").strip()

    if field == "title":
        val = body.value or ""
        if not val:
            rec_titles = [t["text"] for t in rec.get("titles", []) if t.get("recommended")]
            val = rec_titles[0] if rec_titles else ""
        if not val:
            raise HTTPException(400, "no title to apply")
        md["title"] = str(val)[:YT_TITLE_MAX]
    elif field == "description":
        # Spec 06 B2: the fixed block order (hook line, this chapter's summary,
        # arcs, links, series info with every alias, footer, hashtags).
        text, summ = _description_from_blocks(pdir, md, rec, body.variant or "")
        md["description"] = text[:YT_DESC_MAX]
        md["summary_block"] = summ
        md["summary_at"] = time.time()
    elif field == "tags":
        # Spec 06 B4: every alias of the series rides in the tags (500-char cap).
        import series_pack as _sp
        md["tags"] = _sp.tags_with_aliases(list(rec.get("tags") or []),
                                           (_series_pack(pdir) or {}).get("aliases") or [])
    elif field == "hashtags":
        # Hashtags live in the description on YouTube; keep them out of tags.
        text = md.get("description") or ""
        tags = rec.get("hashtags") or []
        keep = "\n".join(l for l in text.splitlines()
                          if not l.strip().startswith("#"))
        md["description"] = (keep.rstrip() + "\n\n" + " ".join(tags)).strip()[:YT_DESC_MAX]
    else:
        raise HTTPException(400, "unknown field %r" % field)

    store[name] = md
    save_publish(pdir, store)
    applied = dict(rec.get("applied") or {})
    applied[field] = {"at": time.time(),
                      "value": md.get("title") if field == "title" else True}
    all_recs = _seo.load_all(pdir)
    if name in all_recs:
        all_recs[name]["applied"] = applied
        _seo.save_all(pdir, all_recs)
    return {"ok": True, "field": field,
            "metadata": md, "problems": validate_publish(md, pdir, name)}


# ====================================================================
#  Custom thumbnails for an export
#  Stored and served here, and sent with the Upload-Post publish as the
#  "thumbnail" file field. See thumbnail.py.
# ====================================================================
@app.post("/api/thumbnail")
async def thumbnail_upload(request: Request, project: str = "", name: str = ""):
    """Raw image bytes in the body.

    Raw rather than multipart on purpose: multipart would pull in
    python-multipart for one endpoint, and the browser can post the file's
    bytes directly just as easily.
    """
    import thumbnail as _tb
    if not name:
        raise HTTPException(400, "which export is this thumbnail for?")
    pdir = project_dir_for(project)
    data = await request.body()
    try:
        rec = _tb.save(pdir, name, data)
    except _tb.ThumbnailError as e:
        # 422, not 500: the upload was understood and refused for a stated
        # reason the operator can act on.
        raise HTTPException(422, str(e))
    return {"ok": True, "thumbnail": rec, "note": _tb.publish_note(True)}


@app.get("/thumbnail")
def thumbnail_get(project: str = "", name: str = ""):
    import thumbnail as _tb
    path = _tb.path_for(project_dir_for(project), name)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "no thumbnail for that export")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.post("/api/thumbnail/delete")
def thumbnail_delete(body: ThumbIn):
    import thumbnail as _tb
    pdir = project_dir_for(body.project)
    removed = _tb.delete(pdir, body.name)
    return {"ok": True, "removed": removed, "note": _tb.publish_note(False)}


# ====================================================================
#  PHASE C — YouTube account connection (SCAFFOLDING)
#  No Google OAuth credentials are configured on this deployment, so the live
#  handshake has never run. Everything here refuses cleanly when unconfigured
#  rather than failing halfway through a connect.
# ====================================================================
def _yt_root():
    import ingest as _ing
    return _ing.PROJECTS


@app.get("/api/youtube/status")
def yt_status():
    """Connection state for the UI. Never returns token material."""
    import youtube as _yt
    return _yt.account_status(_yt_root())


@app.get("/api/youtube/connect")
def yt_connect():
    """Send the operator to Google's consent screen."""
    import youtube as _yt
    from fastapi.responses import RedirectResponse
    cfg = _yt.oauth_config()
    if not cfg["configured"]:
        raise HTTPException(503, "YouTube connection is not available: missing "
                                 + ", ".join(cfg["missing"]) +
                                 ". Create an OAuth client in Google Cloud and "
                                 "set these on Railway.")
    state = _yt.new_state(_yt_root())
    return RedirectResponse(_yt.authorize_url(state, cfg))


@app.get("/api/youtube/callback")
def yt_callback(code: str = "", state: str = "", error: str = ""):
    """Google redirects back here with a one-time code."""
    import youtube as _yt
    from fastapi.responses import RedirectResponse
    if error:
        raise HTTPException(400, f"Google refused the connection: {error}")
    cfg = _yt.oauth_config()
    if not cfg["configured"]:
        raise HTTPException(503, "YouTube connection is not configured")
    if not _yt.check_state(_yt_root(), state):
        # A mismatched state means this callback did not originate from our
        # connect flow — treat it as hostile rather than retrying.
        raise HTTPException(400, "connection state did not match — start again "
                                 "from the Connect button")
    if not code:
        raise HTTPException(400, "no authorization code returned")
    tokens = _yt.exchange_code(code, cfg)
    channel = {}
    try:
        channel = _yt.fetch_channel(tokens.get("access_token"))
    except Exception:
        pass                    # identity is a nicety; the connection still works
    _yt.store_tokens(_yt_root(), tokens, channel)
    return RedirectResponse("/review?connected=1")


@app.post("/api/youtube/disconnect")
def yt_disconnect():
    import youtube as _yt
    removed = _yt.clear_account(_yt_root())
    return {"ok": True, "removed": removed,
            "status": _yt.account_status(_yt_root())}


def upload_eligibility(pdir, name):
    """Every condition that must hold before an upload may start (Phase D).

    Implemented and tested NOW so Phase D inherits a gate that has been
    exercised, rather than one written in the same breath as the upload it is
    supposed to restrain.
    """
    import youtube as _yt
    blockers = []
    stat = _export_stat(pdir, name) if name else None
    if not name or not stat:
        blockers.append("The export file no longer exists.")
    rv = review_state(pdir, name) if name else {"status": "review_pending",
                                                "superseded": False}
    if rv["status"] != "approved":
        blockers.append("This export has not been approved in Review.")
    if rv["superseded"]:
        blockers.append("The cut changed after approval — re-render and "
                        "re-review before uploading.")
    store = load_publish(pdir)
    md = {**publish_defaults(pdir), **(store.get(name) or {})}
    problems = validate_publish(md, pdir, name)
    if problems:
        blockers.append("Publish metadata is incomplete: " + " ".join(problems))
    acct = _yt.account_status(_yt_root())
    if not acct.get("can_upload"):
        blockers.append(acct.get("detail") or "No usable YouTube connection.")
    # Visibility is the operator's choice; private remains the default so it
    # is never raised silently.
    uploads = load_uploads(pdir)
    prior = uploads.get(name) or {}
    if prior.get("status") == "uploaded":
        blockers.append(f"This export was already uploaded "
                        f"({prior.get('video_url') or prior.get('video_id')}).")
    return {"ready": not blockers, "blockers": blockers,
            "review_status": rv["status"], "superseded": rv["superseded"],
            "account": acct, "metadata_problems": problems,
            "already_uploaded": prior or None}


UPLOADS_NAME = "uploads.json"


def load_uploads(pdir):
    try:
        with open(os.path.join(pdir, UPLOADS_NAME), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_uploads(pdir, data):
    tmp = os.path.join(pdir, UPLOADS_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, os.path.join(pdir, UPLOADS_NAME))


@app.get("/api/youtube/eligibility")
def yt_eligibility(project: str = "", name: str = ""):
    pdir = project_dir_for(project)
    pid = os.path.basename(pdir.rstrip("/"))
    if not name:
        name, pid = latest_export(project or pid)
    return {"project": pid, "name": name, **upload_eligibility(pdir, name)}


# ====================================================================
#  PHASE C — publishing accounts via Upload-Post
# ====================================================================
def _os_root():
    import ingest as _ing
    return _ing.PROJECTS


def _get_publish_backend():
    # Upload-Post is the only publisher. When it is not configured,
    # accounts_status() says so and eligibility blocks publishing.
    import upload_post as _up
    return "upload_post", _up


@app.get("/api/publishing/status")
def os_status():
    backend_name, mod = _get_publish_backend()
    return {**mod.accounts_status(_os_root()), "backend": backend_name}


@app.get("/api/publishing/connect")
def os_connect(network: str = "youtube"):
    backend_name, mod = _get_publish_backend()
    from fastapi.responses import RedirectResponse
    cfg = mod.config()
    if not cfg["configured"]:
        raise HTTPException(503, f"{backend_name} is not configured: missing "
                                 + ", ".join(cfg["missing"]))
    try:
        url = mod.get_connect_jwt_url("", cfg=cfg)
    except Exception:
        url = ""
    # Hosted connect not enabled on the account -> the Upload-Post dashboard.
    return RedirectResponse(url or "https://app.upload-post.com/manage-users")


@app.post("/api/publishing/refresh")
def os_refresh():
    """Reconcile local records with provider account list."""
    backend_name, mod = _get_publish_backend()
    cfg = mod.config()
    if not cfg["configured"]:
        raise HTTPException(503, f"{backend_name} is not configured: missing "
                                 + ", ".join(cfg["missing"]))
    try:
        mod.sync_accounts(_os_root(), cfg)
    except Exception as e:
        raise HTTPException(502, str(e))
    return mod.accounts_status(_os_root(), cfg)


class AccountDelIn(BaseModel):
    account_id: str


@app.post("/api/publishing/disconnect")
def os_disconnect(body: AccountDelIn):
    """Forget an account locally."""
    backend_name, mod = _get_publish_backend()
    removed = mod.remove_account(_os_root(), body.account_id)
    if not removed:
        raise HTTPException(404, "account not found")
    return {"ok": True, "removed": body.account_id}


def publish_eligibility(pdir, name):
    """Every condition that must hold before an export may be published."""
    backend_name, mod = _get_publish_backend()
    blockers = []
    stat = _export_stat(pdir, name) if name else None
    if not name or not stat:
        blockers.append("The export file no longer exists.")
    rv = review_state(pdir, name) if name else {"status": "review_pending",
                                                "superseded": False}
    if rv["status"] != "approved":
        blockers.append("This export has not been approved in Review.")
    if rv["superseded"]:
        blockers.append("The cut changed after approval — re-render and re-review before publishing.")
    store = load_publish(pdir)
    md = {**publish_defaults(pdir), **(store.get(name) or {})}
    problems = validate_publish(md, pdir, name)
    if problems:
        blockers.append("Publish metadata is incomplete: " + " ".join(problems))

    acct = mod.accounts_status(_os_root())
    if not acct.get("configured"):
        blockers.append(acct.get("detail") or f"{backend_name} is not configured.")
    elif not acct.get("can_publish"):
        blockers.append("No publishing account is connected.")

    active_ids = {a["account_id"] for a in acct.get("accounts", [])
                  if a.get("active")}
    targets = [t for t in (md.get("targets") or []) if t]
    if not targets:
        blockers.append("Pick at least one connected account to publish to.")
    else:
        gone = [t for t in targets if t not in active_ids]
        if gone:
            blockers.append("These selected accounts are no longer connected: "
                            + ", ".join(gone))

    privacy = md.get("privacy") or "private"
    if privacy not in ("private", "unlisted", "public"):
        blockers.append("Privacy must be one of private, unlisted, public.")

    pubs = load_publishes(pdir)
    prior = pubs.get(name) or {}
    done = [r for r in (prior.get("results") or [])
            if r.get("status") == "published"]
    if done:
        blockers.append("This export was already published to "
                        + ", ".join(r.get("account_id", "?") for r in done))

    return {"ready": not blockers, "blockers": blockers,
            "review_status": rv["status"], "superseded": rv["superseded"],
            "accounts": acct, "targets": targets,
            "metadata_problems": problems,
            "effective_privacy": (md.get("privacy") or "private"),
            "allow_public": True,
            "backend": backend_name,
            "already_published": prior or None}


def _write_private(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


PUBLISHES_NAME = "publishes.json"


def load_publishes(pdir):
    try:
        with open(os.path.join(pdir, PUBLISHES_NAME), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_publishes(pdir, data):
    _write_private(os.path.join(pdir, PUBLISHES_NAME), data)


def _publish_record(pdir, name, **fields):
    pubs = load_publishes(pdir)
    rec = pubs.get(name) or {}
    rec.update(fields)
    rec["updated_at"] = time.time()
    pubs[name] = rec
    save_publishes(pdir, pubs)


def _publish_overall(results):
    if any(r.get("status") == "submitted" for r in results):
        return "in_progress"
    ok = [r for r in results if r.get("status") == "published"]
    return ("published" if results and len(ok) == len(results)
            else ("partial" if ok else "failed"))


def _post_url(hit, network):
    """The post's link. For a private YouTube upload Upload-Post sends a sentence
    ("Post uploaded as Private. No public URL available.") where the URL goes;
    the video id is still there, and the owner can open youtu.be/<id>."""
    u = hit.get("post_url") or ""
    if u.startswith("http"):
        return u
    pid = hit.get("platform_post_id")
    if pid and network == "youtube":
        return f"https://youtu.be/{pid}"
    return u or None


def _resolve_upload_results(mod, results):
    """Ask Upload-Post what the platform did with each submitted target.
    A target stays "submitted" until its request is reported completed."""
    by_req = {}
    for r in results:
        if r.get("status") != "submitted":
            continue
        rid = r.get("request_id")
        if rid not in by_req:
            try:
                by_req[rid] = mod.upload_status(rid)
            except Exception:
                by_req[rid] = None
        st = by_req[rid]
        if not st or st.get("status") != "completed":
            continue
        prof, _, net = r["account_id"].partition(":")
        if not net:
            prof, net = "", prof
        hit = next((x for x in (st.get("results") or [])
                    if x.get("platform") == net
                    and (not prof or x.get("profile_username") == prof)), None)
        if hit is None:
            r.update(status="failed",
                     error="Upload-Post finished but reported no result for this channel")
        elif hit.get("success"):
            r.update(status="published", url=_post_url(hit, net),
                     platform_post_id=hit.get("platform_post_id"),
                     published_at=time.time(), error=None)
        else:
            r.update(status="failed",
                     error=hit.get("error_message") or "the platform rejected the upload")
    return results


def _run_publish_job(job_id, pdir, name):
    backend_name, mod = _get_publish_backend()
    try:
        def step(msg, n):
            _control_gate(JOBS, job_id, _persist_job)
            _publish_record(pdir, name, status="in_progress", stage=msg,
                            stage_num=n)
            JOBS[job_id].update(status="running", stage=msg, done=n)
            _persist_job(job_id)

        elig = publish_eligibility(pdir, name)
        if not elig["ready"]:
            raise RuntimeError("no longer eligible: " + " ".join(elig["blockers"]))

        store = load_publish(pdir)
        md = {**publish_defaults(pdir), **(store.get(name) or {})}
        targets = list(md.get("targets") or [])
        video = os.path.join(pdir, "exports", os.path.basename(name))

        # The custom thumbnail saved for this export (exports/_thumbs/), if any.
        import thumbnail as _tb
        thumb_file = _tb.path_for(pdir, name) or None

        # One Upload-Post request carries ONE profile ("user"). Targets on
        # different profiles (mk:youtube + default:youtube) must be separate
        # uploads, or only the last profile receives the video.
        groups = {}
        for t in targets:
            groups.setdefault(t.split(":", 1)[0] if ":" in t else "", []).append(t)
        names = {x.get("account_id"): x for x in
                 (elig.get("accounts") or {}).get("accounts") or []}
        results = []
        for prof, group in groups.items():
            step("uploading video to Upload-Post (%s)" % ", ".join(group), 1)
            try:
                created = mod.upload_video_post(
                    video, md, group, thumbnail_path=thumb_file,
                    on_step=lambda m: step(m, 1)
                )
            except Exception as e:
                created = {"success": False, "error": str(e)}
            rid = created.get("request_id")
            for t in group:
                a = names.get(t) or {}
                r = {"account_id": t, "request_id": rid, "error": None,
                     "network": a.get("network") or t.partition(":")[2] or t,
                     "username": a.get("username") or t}
                if not created.get("success"):
                    r.update(status="failed",
                             error=created.get("error") or created.get("message")
                             or "Upload-Post rejected the upload")
                elif rid:
                    # Accepted only. The platform's verdict comes from status.
                    r.update(status="submitted")
                else:
                    r.update(status="published", published_at=time.time(),
                             note="accepted; Upload-Post returned no request_id to verify")
                results.append(r)
        _publish_record(pdir, name, status=_publish_overall(results),
                        results=results, stage="waiting for the platform")

        # Wait for the platform's real result (bounded: ~15 min).
        step("waiting for YouTube to confirm", 3)
        for _ in range(90):
            if not any(r["status"] == "submitted" for r in results):
                break
            _control_gate(JOBS, job_id, _persist_job)
            time.sleep(10)
            results = _resolve_upload_results(mod, results)
        overall = _publish_overall(results)
        _publish_record(pdir, name, status=overall, results=results,
                        post_id=next((r.get("request_id") for r in results
                                      if r.get("request_id")), None),
                        stage=("done" if overall != "in_progress" else
                               "still processing on the platform — this page re-checks"),
                        checked_at=time.time(),
                        **({"ended_at": time.time()} if overall != "in_progress" else {}))
        JOBS[job_id].update(status="done", stage=overall, done=4)
        _persist_job(job_id)

    except JobCancelled as e:
        _publish_record(pdir, name, status="cancelled", stage=str(e), ended_at=time.time())
        JOBS[job_id].update(status="cancelled", error=str(e))
        _persist_job(job_id)
    except Exception as e:
        _publish_record(pdir, name, status="failed", error=str(e)[:500], ended_at=time.time(), stage="failed")
        JOBS[job_id].update(status="error", error=str(e)[:500])
        _persist_job(job_id)

class PublishNowIn(BaseModel):
    project: str = ""
    name: str = ""


@app.post("/api/publishing/publish")
def os_publish(body: PublishNowIn):
    """Start a publish. Refuses unless every eligibility condition holds."""
    pdir = project_dir_for(body.project)
    pid = os.path.basename(pdir.rstrip("/"))
    name = body.name or latest_export(body.project or pid)[0]
    if not name:
        raise HTTPException(404, "no export to publish")
    elig = publish_eligibility(pdir, name)
    if not elig["ready"]:
        raise HTTPException(409, "not ready to publish — " + " ".join(elig["blockers"]))
    pubs = load_publishes(pdir)
    if (pubs.get(name) or {}).get("status") == "in_progress":
        raise HTTPException(409, "a publish for this export is already running")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "done": 0, "total": 4, "error": None,
                    "type": "publish", "stage": "queued", "project": pid,
                    "export": name, "ts": time.time(), "control": "run"}
    _persist_job(job_id)
    _publish_record(pdir, name, status="in_progress", job=job_id,
                    stage="queued", started_at=time.time(), results=[])
    threading.Thread(target=_run_publish_job, args=(job_id, pdir, name),
                     daemon=True).start()
    try:                     # every publish is a queue row, wherever it was pressed
        import ingest as _ing
        import publish_queue as _pq0
        _sch = _studio.load().get("schedule") or {}
        _pq0.ensure_posting(_ing.PROJECTS, pid, name, job_id,
                            day=_pq0._local(time.time(), _sch.get("tz")).strftime("%Y-%m-%d"))
    except Exception as e:  # noqa — the publish itself is already running
        print(f"[publish] queue row not updated: {e}", flush=True)
    return {"ok": True, "job": job_id, "project": pid, "name": name,
            # report the visibility this publish will actually use
            "privacy": (load_publish(pdir).get(name) or {}).get("privacy", "private")}


@app.get("/api/publishing/publish/status")
def os_publish_status(project: str = "", name: str = ""):
    pdir = project_dir_for(project)
    pid = os.path.basename(pdir.rstrip("/"))
    if not name:
        name, pid = latest_export(project or pid)
    rec = (load_publishes(pdir) or {}).get(name) or {}
    job = JOBS.get(rec.get("job") or "") or {}
    if (rec.get("status") == "in_progress" and job.get("status") not in ("queued", "running")
            and any(r.get("status") == "submitted" for r in rec.get("results") or [])
            and time.time() - (rec.get("checked_at") or 0) > 15):
        _b, mod = _get_publish_backend()
        results = _resolve_upload_results(mod, rec["results"])
        overall = _publish_overall(results)
        _publish_record(pdir, name, status=overall, results=results,
                        checked_at=time.time(),
                        **({"stage": "done", "ended_at": time.time()}
                           if overall != "in_progress" else {}))
        rec = (load_publishes(pdir) or {}).get(name) or {}
    return {"project": pid, "name": name, "publish": rec or None}


@app.get("/api/publishing/eligibility")
def os_eligibility(project: str = "", name: str = ""):
    pdir = project_dir_for(project)
    pid = os.path.basename(pdir.rstrip("/"))
    if not name:
        name, pid = latest_export(project or pid)
    return {"project": pid, "name": name, **publish_eligibility(pdir, name)}


# ---- installable on a phone home screen --------------------------------
# A manifest + icon let the studio be "Added to Home Screen" and open
# full-screen without browser bars. Not secret: no project data, and the site
# gate (Vercel Basic Auth) still sits in front of every path.
_APP_ICON_CACHE = {}


def _app_icon_png(size):
    if size not in _APP_ICON_CACHE:
        import io
        from PIL import Image, ImageDraw
        k = size / 512.0
        im = Image.new("RGB", (size, size), "#0b0e14")
        d = ImageDraw.Draw(im)
        # three manhwa panels stacked like a webtoon strip
        for x0, y0, x1, y1 in ((96, 84, 416, 196), (96, 216, 250, 428), (270, 216, 416, 428)):
            d.rounded_rectangle([x0 * k, y0 * k, x1 * k, y1 * k], radius=18 * k,
                                fill="#1b2130", outline="#2e3648", width=max(1, int(6 * k)))
        # play mark over the strip: it becomes a video
        d.polygon([(214 * k, 236 * k), (214 * k, 400 * k), (352 * k, 318 * k)], fill="#00d5ff")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        _APP_ICON_CACHE[size] = buf.getvalue()
    return _APP_ICON_CACHE[size]


@app.get("/app-icon/{size}.png")
def app_icon(size: int):
    from fastapi.responses import Response
    if size not in (180, 192, 512):
        raise HTTPException(404, "no such icon size")
    return Response(_app_icon_png(size), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/manifest.webmanifest")
def web_manifest():
    from fastapi.responses import JSONResponse as _J
    return _J({
        "name": "Manhwa Recap Studio",
        "short_name": "Recap Studio",
        "start_url": "/storyboard",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0b0e14",
        "theme_color": "#141824",
        "icons": [
            {"src": "/app-icon/192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/app-icon/512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }, media_type="application/manifest+json")


# ---- narrator voice: options, studio default, previews -----------------
_PREVIEW_LINE = ("Yu Shin kept his head down, already feeling the cold pressure "
                 "of a hostile gaze. Then he smiled.")


@app.get("/api/voices")
def api_voices():
    import gemini_tts as _gt
    import ingest as _ing
    cur = _gt.get_tts_engine_config(_ing.PROJECTS)
    return {"voices": _gt.voice_options(),
            "styles": [{"style": st, "label": lb} for st, lb in _gt.STYLE_PRESETS],
            "default": {"id": _gt.choice_id(cur), "style": cur.get("style", ""),
                        "saved": bool(_gt.load_default(_ing.PROJECTS))}}


class VoiceIn(BaseModel):
    voice: str
    style: str | None = None


@app.post("/api/voices/default")
def api_voices_default(body: VoiceIn):
    import gemini_tts as _gt
    import ingest as _ing
    try:
        rec = _gt.save_default(_ing.PROJECTS, body.voice, body.style)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _ev("settings", f"Studio voice set to {_voice_label(rec)}. Chapters voiced differently are "
                    f"re-recorded (paid) when rendered, unless rendered with their own voice.")
    return {"ok": True, "default": {"id": _gt.choice_id(rec), "style": rec.get("style", "")}}


@app.post("/api/voices/preview")
def api_voices_preview(body: VoiceIn):
    """Record the sample line in one voice + style (cached, so replaying a
    preview is free) and return where to play it from."""
    import hashlib as _h
    import gemini_tts as _gt
    import ingest as _ing
    try:
        rec = _gt.parse_choice(body.voice, body.style)
    except ValueError as e:
        raise HTTPException(400, str(e))
    key = _gt.env_any_case("TTS_API_KEY" if rec["provider"] == "chirp" else "GEMINI_API_KEY")
    if not key:
        raise HTTPException(503, "that voice's API key is not configured")
    eng = (_gt._chirp_cfg(key) if rec["provider"] == "chirp" else
           {"provider": "gemini", "api_key": key, "model": rec["model"],
            "voice": rec["voice"], "style": rec["style"]})
    d = os.path.join(_ing.PROJECTS, "_voice_previews")
    os.makedirs(d, exist_ok=True)
    name = _h.sha1(("%s|%s|%s" % (body.voice, rec["style"], _PREVIEW_LINE)).encode()).hexdigest()[:20] + ".mp3"
    out = os.path.join(d, name)
    if not os.path.exists(out):
        try:
            _synth_rest(_PREVIEW_LINE, out, style=rec["style"], engine=eng)
        except usage.UsageCapExceeded as e:
            raise HTTPException(429, str(e))
        except Exception as e:
            raise HTTPException(502, f"could not record the preview: {type(e).__name__}")
    return {"url": f"/api/voices/preview/{name}", "text": _PREVIEW_LINE}


@app.get("/api/voices/preview/{name}")
def api_voices_preview_file(name: str):
    import re as _re
    import ingest as _ing
    if not _re.fullmatch(r"[0-9a-f]{20}\.mp3", name):
        raise HTTPException(404, "not found")
    p = os.path.join(_ing.PROJECTS, "_voice_previews", name)
    if not os.path.exists(p):
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="audio/mpeg")


@app.get("/review")
def review_page():
    import review_page as _rp
    project = ""
    return HTMLResponse(_rp.build_review_html(active_project_dir()))


class ExportDelIn(BaseModel):
    name: str
    project: str = ""


# ---- ready to publish by itself (owner, 2026-10-05: "Isn't there supposed to
# be SEO? And thumbnails? Take a look at the scrapper") ----------------------
# Like Scrapper's LongForm: once a video exists, its title / description /
# tags are filled from the SEO suggestions and its thumbnail options are made
# with the best one picked — without a click. Only fields still at their
# automatic default are filled; anything the owner typed is never replaced.
_PREPARING = set()


class _SkipSeo(Exception):
    pass


def _budget_left():
    try:
        return float(usage.daily_cap()) - float(usage.daily_summary().get("est_cost_usd") or 0)
    except Exception:
        return 0.0


def _publish_prep_pass(limit=5):
    """Scheduler step: chapters waiting for you that still lack SEO or a
    thumbnail get them (e.g. after the spend day resets). Bounded per pass;
    with no budget left it only makes thumbnails (free) and stays quiet."""
    import seo as _seo
    import thumbnail as _tb
    have_budget = _budget_left() > 0.10
    n = 0
    for r in _chapter_rows():
        if n >= limit:
            break
        if r["status"]["key"] not in ("to_review", "video_ready", "scheduled"):
            continue
        pdir = project_dir_for(r["id"])
        name = _cs._latest_export(pdir) or DRAFT
        need_seo = not (_seo.get(pdir, name) or _seo.get(pdir, DRAFT))
        need_thumb = name != DRAFT and not _tb.path_for(pdir, name)
        if (need_seo and have_budget) or need_thumb:
            _prepare_publish(pdir, name, do_seo=need_seo and have_budget)
            n += 1
_PREP_NOTE = {}        # (pid, name) -> why SEO isn't filled yet, shown on the page


def _prepare_publish(pdir, name, do_seo=True):
    """SEO (one small model call, budget-gated) then thumbnail options (free,
    local). Never raises: a failure is logged and the video is unaffected.
    name == DRAFT: the chapter has no video yet — SEO only (as Scrapper writes
    the title/description/tags while making, before the render)."""
    import seo as _seo
    import thumbnail as _tb
    import thumbnail_studio as tstudio
    pid = os.path.basename(pdir.rstrip("/"))
    key = (pid, name)
    if key in _PREPARING:
        return
    _PREPARING.add(key)
    label = _pretty(pid)
    filled = []
    try:
        try:
            if not do_seo:
                raise _SkipSeo()
            if name != DRAFT and _series_locked(pdir)[1]:
                # Spec 10 Stage 1-3: the series-locked package (no free-form SEO)
                import gemini_tts as _gt
                _apply_chapter_package(pdir, name, _build_chapter_package(
                    pdir, name, _gt.env_any_case("GEMINI_API_KEY")))
                filled += ["title", "description", "tags"]
                raise _SkipSeo()
            if not (_seo.get(pdir, name) or _seo.get(pdir, DRAFT)):
                api_seo_generate(SeoGenIn(project=pid, name=name))
            base = _publish_defaults_base(pdir)
            md = {**publish_defaults(pdir), **(load_publish(pdir).get(name) or {})}
            # Owner, 2026-10-07: "Use for series" decides these for every chapter;
            # the automatic SEO fill-in never replaces what the series defaults set.
            import series_pack as _spk
            _sp_pack = _spk.load(_yt_root(), _series_ident(pdir)[0]) or {}
            locked = {"title": bool(_sp_pack.get("title_template")),
                      "description": bool(_sp_pack.get("description_template")),
                      "tags": bool(_sp_pack.get("default_tags"))}
            for field in ("title", "description", "tags"):
                if locked[field]:
                    continue
                if not md.get(field) or md.get(field) == base.get(field):
                    try:
                        api_seo_apply(SeoApplyIn(project=pid, name=name, field=field))
                        filled.append(field)
                    except HTTPException:
                        pass
            _PREP_NOTE.pop(key, None)
        except _SkipSeo:
            pass
        except usage.UsageCapExceeded as e:
            _PREP_NOTE[key] = ("Title, description and tags will fill in after midnight ET — today's spend "
                               "limit is used up (the suggestions cost a few cents; the web research is reused for a week). The thumbnail is ready.")
            _ev("publish", f"{label}: SEO suggestions wait for budget — {str(e)[:120]}", "warn")
        except Exception as e:  # noqa
            _PREP_NOTE[key] = f"Title suggestions couldn't be made: {str(e)[:160]}"
            _ev("publish", f"{label}: SEO suggestions failed — {str(e)[:160]}", "warn")
        try:
            if name == DRAFT:
                raise _SkipSeo()                  # thumbnails are made from the video
            if not (tstudio.get_concepts(pdir, name) or {}).get("concepts"):
                api_thumbcopilot_generate(ThumbGenIn(project=pid, name=name))
            if not _tb.path_for(pdir, name):
                cs = (tstudio.get_concepts(pdir, name) or {}).get("concepts") or []
                best = next((c for c in cs if c.get("recommended")), cs[0] if cs else None)
                if best:
                    api_thumbcopilot_apply(ThumbApplyIn(project=pid, name=name, concept_id=best["id"]))
                    filled.append("thumbnail")
        except _SkipSeo:
            pass
        except Exception as e:  # noqa
            _ev("publish", f"{label}: thumbnail options failed — {str(e)[:160]}", "warn")
        if filled:
            _ev("publish", f"{label}: ready to review — filled in {', '.join(filled)} (change anything on Watch the video)", "ok")
    finally:
        _PREPARING.discard(key)


def _after_export(pdir, name):
    """Background, after an export: prepare it for publishing, then copy it
    (with its thumbnail) to Drive."""
    _prepare_publish(pdir, name)
    try:
        _qc_bundle(pdir, name)        # warm the Watch tab's checks so it opens at once
    except Exception:
        pass
    if name and _drive.configured():
        _drive_copy(pdir, name)


class PrepareIn(BaseModel):
    project: str
    name: str


@app.post("/api/publish/prepare")
def api_publish_prepare(body: PrepareIn):
    """Run the automatic SEO + thumbnail step for a video made before it existed."""
    pdir = project_dir_for(body.project)
    name = os.path.basename(body.name)
    if not os.path.exists(os.path.join(pdir, "exports", name)):
        raise HTTPException(404, "video not found")
    threading.Thread(target=_prepare_publish, args=(pdir, name), daemon=True).start()
    return {"ok": True}


# ---- phone playback (owner, 2026-10-05) ----------------------------------
# Exports sped up before ddf0e15 are 37.5 fps tagged H.264 level 6.2, which
# iPhones refuse to play. "Fix for phones" re-encodes the same file in place
# (30 fps, level 4.1, index first), free, then re-copies it to Drive.
_PHONE_OK = {}


def _phone_ok(path):
    """True/False from ffprobe (cached by size+mtime); None if it can't tell."""
    try:
        st_ = os.stat(path)
    except OSError:
        return None
    key = (path, st_.st_size, st_.st_mtime_ns)
    if key not in _PHONE_OK:
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=level,r_frame_rate", "-of", "json", path],
                               capture_output=True, text=True, timeout=20)
            v = json.loads(r.stdout)["streams"][0]
            num, den = (v.get("r_frame_rate") or "0/1").split("/")
            fps = float(num) / float(den or 1)
            _PHONE_OK[key] = int(v.get("level") or 0) <= 42 and fps <= 30.5
        except Exception:
            _PHONE_OK[key] = None
    return _PHONE_OK[key]


def _phonefix_job(pid, name):
    for jid, x in list(JOBS.items()):
        if x.get("type") == "phonefix" and x.get("project") == pid and x.get("name") == name \
                and x.get("status") in ("queued", "running"):
            return {"job": jid, "status": x["status"], "pct": x.get("pct")}
    return None


def _run_phonefix(job_id, pdir, name):
    j = JOBS[job_id]
    j["status"] = "running"
    _persist_job(job_id)
    src = os.path.join(pdir, "exports", name)
    tmp = src + ".phone.partial"       # not *.mp4, so it never lists as a video
    label = _pretty(j["project"])
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                    "csv=p=0", src], capture_output=True, text=True, timeout=30).stdout or 0)
        cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", "-i", src,
               "-map", "0:v:0", "-map", "0:a?", "-vf", "fps=30", "-c:v", "libx264", "-preset", "medium",
               "-crf", "18", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", "4.1",
               "-c:a", "copy", "-movflags", "+faststart", "-f", "mp4", tmp]
        pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in pr.stdout:
            if line.startswith("out_time_us=") and dur:
                try:
                    j["pct"] = min(99, round(100 * int(line.split("=")[1]) / 1e6 / dur))
                    j["done"], j["total"] = j["pct"], 100
                except ValueError:
                    pass
            if j.get("control") == "stop":
                pr.kill()
                raise RuntimeError("stopped by you")
        if pr.wait() != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1000:
            raise RuntimeError("ffmpeg failed: " + (pr.stderr.read() or "")[-200:])
        st_ = os.stat(src)
        os.replace(tmp, src)                 # same name: review, SEO and queue rows stay attached
        os.utime(src, (st_.st_atime, st_.st_mtime))   # keep its date (retention, "newest")
        j.update(status="done", pct=100, ended=time.time())
        _ev("render", f"{label}: {name} re-encoded so it plays on phones", "ok")
        rec = _drive.record(pdir) or {}
        if rec.get("video") == name:
            _drive_copy_later(pdir, name)     # replace the Drive copy too
    except Exception as e:  # noqa
        j.update(status="error", error=str(e)[:300], ended=time.time())
        _ev("render", f"{label}: phone fix failed — {str(e)[:160]}", "error")
        try:
            os.remove(tmp)
        except OSError:
            pass
    _persist_job(job_id)


class PhoneFixIn(BaseModel):
    project: str
    name: str


@app.post("/api/exports/phonefix")
def exports_phonefix(body: PhoneFixIn):
    pdir = project_dir_for(body.project)
    pid = os.path.basename(pdir.rstrip("/"))
    name = os.path.basename(body.name)
    path = os.path.join(pdir, "exports", name)
    if not name.endswith(".mp4") or not os.path.exists(path):
        raise HTTPException(404, "video not found")
    running = _phonefix_job(pid, name)
    if running:
        return {"ok": True, "job": running["job"], "already": True}
    if _phone_ok(path):
        return {"ok": True, "job": None, "note": "it already plays on phones"}
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"type": "phonefix", "status": "queued", "project": pid, "name": name,
                    "stage": "re-encoding for phones", "pct": 0, "done": 0, "total": 100, "ts": time.time()}
    _persist_job(job_id)
    threading.Thread(target=_run_phonefix, args=(job_id, pdir, name), daemon=True).start()
    return {"ok": True, "job": job_id}


@app.post("/api/exports/delete")
def delete_export(body: ExportDelIn):
    """Remove one export now, rather than waiting out the retention window."""
    import ingest as _ing
    safe = os.path.basename(body.name)
    if body.project and ("/" in body.project or ".." in body.project):
        raise HTTPException(400, "bad project id")
    d = (os.path.join(_ing.PROJECTS, body.project, "exports")
         if body.project else active_exports_dir())
    path = os.path.join(d, safe)
    if not os.path.exists(path):
        raise HTTPException(404, "export not found")
    os.remove(path)
    return {"ok": True, "deleted": safe, "project": body.project}


class ProjectDelIn(BaseModel):
    id: str = ""
    ids: list[str] = []          # bulk select-and-delete from the Projects tab


GONE_NAME = "_gone.json"


def _note_gone(pdir, why):
    """Remember a chapter whose folder is removed, so the Library can still
    say it was made and why it's gone (owner, 2026-10-07: chapters made then
    cleaned up showed as 'Not made' with a Make button)."""
    import ingest as _ing
    meta = _read_json(os.path.join(pdir, "project.json")) or {}
    posted = None
    try:
        for rec in (load_publishes(pdir) or {}).values():
            for r in rec.get("results") or []:
                if r.get("status") == "published":
                    posted = _post_url({"post_url": r.get("url"), "platform_post_id": r.get("platform_post_id")},
                                       r.get("network") or "youtube") or True
    except Exception:
        pass
    path = os.path.join(_ing.PROJECTS, GONE_NAME)
    data = _read_json(path) or {}
    data[os.path.basename(pdir.rstrip("/"))] = {"at": time.time(), "why": why, "url": meta.get("url") or "",
                                                "series": meta.get("series"), "chapter": str(meta.get("chapter") or ""),
                                                "posted": posted}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def _delete_one_project(pid, why="deleted by you"):
    """Remove one project dir. Returns (ok, detail_or_reason, freed_mb)."""
    import ingest as _ing
    import shutil as _sh
    if not pid or "/" in pid or ".." in pid or pid.startswith("_"):
        return False, "bad project id", 0.0
    pdir = os.path.join(_ing.PROJECTS, pid)
    if not os.path.isdir(pdir):
        return False, "unknown project", 0.0
    busy = _chapter_busy(pdir)
    if busy:
        return False, busy + " — stop it first", 0.0
    try:
        import publish_queue as _pqd
        if any(x["project"] == pid and x["status"] in ("queued", "posting") for x in _pqd.load(_ing.PROJECTS)["items"]):
            return False, "scheduled to post — take it off the queue first", 0.0
    except Exception:
        pass
    if os.path.abspath(pdir) == os.path.abspath(active_project_dir()):
        # the new studio opens whatever chapter you look at; move the studio
        # to another chapter instead of refusing (2026-10-04)
        others = sorted((p for p in os.listdir(_ing.PROJECTS) if p != pid and not p.startswith("_")
                         and os.path.exists(os.path.join(_ing.PROJECTS, p, "segments.json"))),
                        key=lambda p: -os.path.getmtime(os.path.join(_ing.PROJECTS, p, "segments.json")))
        if not others:
            return False, "this is the only chapter — it can't be deleted while open", 0.0
        activate_project(ActivateIn(id=others[0]))
    size = 0
    for root, _dirs, files in os.walk(pdir):
        for fn in files:
            try:
                size += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    try:
        _note_gone(pdir, why)
    except Exception as e:  # noqa — the record is a courtesy; never block a delete
        print(f"[gone] {pid}: {str(e)[:160]}", flush=True)
    _sh.rmtree(pdir)
    return True, "deleted", round(size / 1e6, 1)


@app.post("/api/projects/delete")
def delete_project(body: ProjectDelIn):
    """Delete one project, or several at once from the Projects tab.

    Bulk is partial-success by design: one undeletable project (the open one)
    must not abort the rest, so every id gets its own result. The ACTIVE
    project is always refused — deleting the data the studio is serving would
    leave every route pointing at a missing directory.
    """
    ids = [i for i in (list(body.ids) + ([body.id] if body.id else [])) if i]
    seen, ordered = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    if not ordered:
        raise HTTPException(400, "no project id given")
    deleted, skipped, freed = [], [], 0.0
    for pid in ordered:
        ok, why, mb = _delete_one_project(pid)
        if ok:
            deleted.append(pid)
            freed += mb
        else:
            skipped.append({"id": pid, "reason": why})
    # a single-id call that failed should still surface as an error
    if len(ordered) == 1 and not deleted:
        code = 409 if "currently open" in skipped[0]["reason"] else (
            404 if skipped[0]["reason"] == "unknown project" else 400)
        raise HTTPException(code, skipped[0]["reason"])
    return {"ok": True, "deleted": deleted, "skipped": skipped,
            "freed_mb": round(freed, 1)}


@app.post("/api/render-missing")
def render_missing(force: bool = False):
    """Render any segment that lacks a clip, via render_segments.py --only.

    Gated by storyboard approval (D1): bulk render spend happens only after
    the storyboard has been reviewed — pass ?force=true to override."""
    if not force and not storyboard_approved():
        raise HTTPException(409, "storyboard not approved — review /storyboard "
                                 "first, or call with ?force=true")
    segs = load_segments()
    pdir = active_project_dir()
    missing = [s["seg_index"] for s in segs
               if s.get("user_included")
               and not os.path.exists(os.path.join(pdir, s.get("clip", "")))]
    if not any(s.get("user_included") for s in segs):
        raise HTTPException(400, "nothing is ticked for the final video — "
                                 "tick segments on /storyboard first")
    # P0 (Session 25): pre-render gate. The renderer now refuses a segment
    # whose audio does not fit its window, so catch it here with a useful
    # message instead of a mid-batch RuntimeError.
    _gate_timeline(missing, "render")
    done = []
    for i in missing:
        env = os.environ.copy()
        env["HF_WORKSPACE"] = pdir
        env["HF_AUDIO_DIR"] = AUDIO_DIR
        env["HF_PANELS_DIR"] = os.path.join(pdir, "crops")
        r = subprocess.run(
            [sys.executable, os.path.join(HF, "render_segments.py"),
             "--only", str(i)],
            capture_output=True, text=True, env=env)
        if r.returncode == 0:
            done.append(i)
    return {"ok": True, "rendered": done, "still_missing": len(missing) - len(done)}


# ================================================================ MVP-2
# Direct edits. The review UI's segments.json IS the editable state. Each edit
# snapshots segments.json (undo), mutates it, then re-renders ONLY the affected
# clip — segments are the isolation boundary (a clip contains its own beats'
# audio and plays sequentially in the concat), so no downstream clip is touched.

# Project wiring — derived from the loaded segments + env overrides. The panel
# dir comes from the segments' own panel_file paths; audio + descriptions match
# the chapter this workspace was built from.
def _panel_dir(pdir=None):
    segs = _segments_of(pdir) if pdir else load_segments()
    if segs and segs[0].get("panel_file"):
        return os.path.dirname(segs[0]["panel_file"])
    return os.path.join(RECAP, "..", "panel-split", "review_crops")

AUDIO_DIR = os.environ.get(
    "REVIEW_AUDIO_DIR", os.path.join(RECAP, "build_test", "tts_ch2"))
DESCRIPTIONS = os.environ.get(
    "REVIEW_DESCRIPTIONS",
    os.path.join(RECAP, "..", "panel-describe", "descriptions_ch2.json"))


def init_active_project():
    global AUDIO_DIR, DESCRIPTIONS
    pid = get_active_project_id()
    if pid:
        import ingest
        proj = os.path.join(ingest.PROJECTS, pid)
        if os.path.exists(proj):
            AUDIO_DIR = os.path.join(proj, "audio")
            pdesc = os.path.join(proj, "descriptions.json")
            if os.path.exists(pdesc):
                DESCRIPTIONS = pdesc
            print(f"--- Restored persistent active project: {pid} ---", file=sys.stderr)


init_active_project()

VERSIONS = os.path.join(HERE, "versions")
os.makedirs(VERSIONS, exist_ok=True)

sys.path.insert(0, RECAP)
sys.path.insert(0, HF)


def _load_descriptions():
    try:
        with open(DESCRIPTIONS, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _snapshot():
    """Save current segments.json as a numbered version for undo."""
    segs = load_segments()
    n = len([f for f in os.listdir(VERSIONS) if f.endswith(".json")])
    with open(os.path.join(VERSIONS, f"v{n:04d}.json"), "w") as f:
        json.dump(segs, f)


def _epochs_path():
    return os.path.join(active_project_dir(), "clips", ".render_epochs.json")


def _load_epochs():
    try:
        with open(_epochs_path(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _stamp_epoch(si):
    """Record which renderer version produced this clip."""
    import render_segments as rs
    d = _load_epochs()
    d[str(si)] = getattr(rs, "RENDER_EPOCH", 1)
    os.makedirs(os.path.dirname(_epochs_path()), exist_ok=True)
    tmp = _epochs_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, _epochs_path())


def needs_render(segs, pdir=None):
    """Segments whose clip is missing OR was made by an older renderer."""
    import render_segments as rs
    cur = getattr(rs, "RENDER_EPOCH", 1)
    pdir = pdir or active_project_dir()
    ep = _load_epochs()
    out = []
    for s in segs:
        clip = os.path.join(pdir, s.get("clip", ""))
        if not os.path.exists(clip) or ep.get(str(s["seg_index"]), 1) != cur:
            out.append(s["seg_index"])
    return out


import threading  # also imported further down; needed here first
_EPOCH_LOCK = threading.Lock()


def _rerender(seg, workdir=None):
    """Re-render one segment's clip from the (edited) seg dict.
    workdir: an isolated dir for the hyperframes page when clips render in
    parallel (they would otherwise share one index.html)."""
    import render_segments as rs
    rs.PANEL_DIR = _panel_dir()
    rs.WORK = active_project_dir()
    rs.CLIPS = os.path.join(rs.WORK, "clips")
    rs.ASSETS = os.path.join(rs.WORK, "assets")
    rs.ensure_project()
    rs.render_segment(seg, AUDIO_DIR, workdir=workdir)
    with _EPOCH_LOCK:          # read-modify-write of one shared json
        _stamp_epoch(seg["seg_index"])
    # thumbnail may be stale after a panel swap / re-crop — the filename is
    # keyed by framing, so clear EVERY thumb for this segment, not one name
    import glob
    for tp in glob.glob(os.path.join(active_project_dir(), "thumbnails",
                                     f"seg_{seg['seg_index']:03d}*.jpg")):
        try:
            os.remove(tp)
        except OSError:
            pass


@app.get("/api/segments/{seg_index}/candidates")
def candidates(seg_index: int, k: int = 8):
    """Top-K alternative panels for this segment, ranked by text similarity to
    its narration — the matcher's shortlist, so a swap is a click not a guess."""
    import matcher
    segs = load_segments()
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    if not seg:
        raise HTTPException(404, "segment not found")
    text = " ".join(b["text"] for b in seg.get("beats", []))
    panels = [p for p in _load_descriptions()
              if p.get("ok", True) and not matcher.is_junk_panel(p)]
    scored = []
    for p in panels:
        ptext = f"{p.get('visual_description','')} {p.get('ocr_text','')}"
        scored.append((matcher._lexical_sim(text, ptext), p))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, p in scored[:k]:
        out.append({
            "panel_id": p["panel_id"],
            "score": round(score, 3),
            "current": p["panel_id"] == seg["panel_id"],
            "ocr": (p.get("ocr_text") or "")[:60],
            "desc": (p.get("visual_description") or "")[:80],
            "thumb_url": f"/panelimg/{p['panel_id']}?thumb=1",
        })
    return {"seg_index": seg_index, "current": seg["panel_id"], "candidates": out}


@app.get("/panelimg/{panel_id}")
def panelimg(panel_id: str, thumb: int = 0, project: str = ""):
    pdir = _media_pdir(project)
    path = os.path.join(_panel_dir(pdir if project else None), f"{os.path.basename(panel_id)}.png")
    if not os.path.exists(path):
        raise HTTPException(404, "panel not found")
    if not thumb:
        return FileResponse(path, media_type="image/png")
    t_dir = os.path.join(pdir, "thumbnails")
    os.makedirs(t_dir, exist_ok=True)
    tp = os.path.join(t_dir, f"panel_{panel_id}.jpg")
    if not os.path.exists(tp):
        try:
            subprocess.run(["ffmpeg", "-y", "-i", path, "-vf", "scale=160:-1",
                            "-frames:v", "1", tp], check=True, capture_output=True)
        except Exception:
            raise HTTPException(404, "thumb failed")
    return FileResponse(tp, media_type="image/jpeg")


@app.get("/segimg/{seg_index}")
def segimg(seg_index: int, full: int = 0, project: str = ""):
    """The frame the VIDEO shows for this segment, at full resolution.

    P2 (Session 25): /panelimg is keyed by PANEL and knows nothing about the
    crop, but crop_bbox_norm is a property of the SEGMENT — one panel can feed
    several segments with different crops. This route is the segment-accurate
    view the storyboard links to. `?full=1` returns the uncropped panel so the
    original art stays one click away.
    """
    pdir = _media_pdir(project)
    segs = _segments_of(pdir)
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    if not seg:
        raise HTTPException(404, "segment not found")
    src = seg.get("panel_file")
    if not src or not os.path.exists(src):
        raise HTTPException(404, "panel image not found")
    rect = None if full else seg_crop_rect(seg)
    if not rect:
        return FileResponse(src, media_type="image/png")
    x, y, w, h = rect
    c_dir = os.path.join(pdir, "thumbnails")
    os.makedirs(c_dir, exist_ok=True)
    out = os.path.join(c_dir, f"crop_{seg_index:03d}_{_thumb_key(seg)}.png")
    if not os.path.exists(out):
        try:
            subprocess.run(["ffmpeg", "-y", "-i", src, "-vf",
                            f"crop={w}:{h}:{x}:{y}", "-frames:v", "1", out],
                           check=True, capture_output=True)
        except Exception:
            return FileResponse(src, media_type="image/png")   # safe fallback
    return FileResponse(out, media_type="image/png")


class SwapIn(BaseModel):
    panel_id: str


@app.post("/api/segments/{seg_index}/panel")
def swap_panel(seg_index: int, body: SwapIn):
    segs = load_segments()
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    if not seg:
        raise HTTPException(404, "segment not found")
    new_path = os.path.join(_panel_dir(), f"{body.panel_id}.png")
    if not os.path.exists(new_path):
        raise HTTPException(400, f"panel {body.panel_id} not found")
    _snapshot()
    seg["panel_id"] = body.panel_id
    seg["panel_file"] = new_path
    _write_segments(segs)
    job = _start_render([seg_index])   # clip re-renders in background; preview is instant
    return {"ok": True, "seg_index": seg_index, "panel_id": body.panel_id, "job": job}


# --- narration edit → re-TTS (REST; the google-cloud SDK import hangs) -----
GAP_SEC = 0.35  # inter-beat gap, matches tts.GAP_SEC


def _tts_key():
    # Production (Railway etc.) sets TTS_API_KEY as a real env var — no .env
    # file exists in the container, so check the environment FIRST. Only fall
    # back to reading a local .env (for bare local dev) if one is present, and
    # never crash with a raw FileNotFoundError if neither source has the key.
    env_key = os.environ.get("TTS_API_KEY")
    if env_key:
        return env_key
    dotenv_path = os.path.join(RECAP, "..", ".env")
    if os.path.exists(dotenv_path):
        for line in open(dotenv_path):
            if line.startswith("TTS_API_KEY"):
                return line.split("=", 1)[1].strip()
    return None


_TTS_VOICE = "Charon"
_TTS_CACHE_DIR = os.path.join(HERE, "projects", "_ttscache")


def _synth_rest(text, out_path, style=None, engine=None):
    """Synthesize one beat to MP3 in the voice its PROJECT is pinned to:
    Gemini 3.8 Flash TTS for projects voiced from 2026-10-02 on, the legacy
    Chirp voice for older ones (gemini_tts.engine_for_project). `engine` is
    that resolved config; when omitted, the active project's is used.

    E2: results are cached by content hash in projects/_ttscache, so a script
    edit re-synthesizes ONLY the sentences that actually changed and identical
    text across re-ingests costs nothing. Chirp keeps its original key
    (voice|text) so the lines already recorded stay reusable."""
    import base64, hashlib, shutil, ssl, urllib.request
    import speech_text
    import gemini_tts

    # What the narrator SAYS: no quotation marks, harsher profanity softened
    # (docs/craft_reconciliation.md §7g, owner decision Q4). Done before the
    # cache key, so a quote-only change to the script costs no new TTS.
    text = speech_text.speakable(text)
    os.makedirs(_TTS_CACHE_DIR, exist_ok=True)

    engine_cfg = engine or gemini_tts.engine_for_project(active_project_dir())
    provider = engine_cfg["provider"]
    voice, model = engine_cfg["voice"], engine_cfg["model"]
    chosen_style = style if style is not None else engine_cfg.get("style", "")

    if provider == "chirp":
        ck = hashlib.sha1(f"{gemini_tts.CHIRP_VOICE}|{text}".encode()).hexdigest()
    else:
        ck = hashlib.sha1(f"{provider}|{model}|{voice}|{chosen_style}|{text}".encode()).hexdigest()
    cached = os.path.join(_TTS_CACHE_DIR, f"{ck}.mp3")
    if os.path.exists(cached) and os.path.getsize(cached) > 0:
        shutil.copyfile(cached, out_path)
        return

    def _cache_put(src_bytes):
        # Atomic: lines are recorded in parallel, and another thread may be
        # copying this same cache file at this moment.
        try:
            tmp = "%s.%d.%d.tmp" % (cached, os.getpid(), threading.get_ident())
            with open(tmp, "wb") as f:
                f.write(src_bytes)
            os.replace(tmp, cached)
        except OSError:
            pass

    if provider == "gemini":
        with usage.gate("tts", len(text), model=model) as _m:
            info = gemini_tts.synth_gemini_tts(
                text=text, out_path=out_path, cfg=engine_cfg,
                style=chosen_style, voice=voice)
            _m.tokens(info["prompt_tokens"], info["audio_tokens"])
        with open(out_path, "rb") as f:
            _cache_put(f.read())
        return

    # Legacy Chirp voice (projects voiced before the switch)
    key = engine_cfg.get("api_key") or _tts_key()
    if not key:
        raise RuntimeError("TTS_API_KEY not set (checked env var and local .env)")
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    body = {"input": {"text": text},
            "voice": {"languageCode": "en-US", "name": gemini_tts.CHIRP_VOICE},
            "audioConfig": {"audioEncoding": "MP3"}}
    req = urllib.request.Request(
        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})

    def _call():
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            return base64.b64decode(json.load(r)["audioContent"])

    with usage.gate("tts", len(text), model=gemini_tts.CHIRP_MODEL):
        audio = _call()
    with open(out_path, "wb") as f:
        f.write(audio)
    _cache_put(audio)


def _recompute_timeline(segs):
    """Editor-aware timeline rebuild (storyboard v2). The VISUAL track is
    authoritative: each segment keeps its (possibly user-set) duration —
    including silent holds with no beats at all — and only grows when its
    narration audio genuinely overflows it. Per-beat durations are re-read
    from the actual audio files (which may be slice parts carrying an
    explicit "file"), so a re-TTS resizes exactly the segment it lives in,
    and everything downstream shifts by the difference."""
    import render_segments as rs
    t = 0.0
    for seg in segs:
        delta = round(t - seg.get("start", 0), 3)
        seg["start"] = round(t, 3)
        prev_end = None
        for b in seg["beats"]:
            gap = GAP_SEC if prev_end is None else max(0.0, round(b["start"] + delta, 3) - prev_end)
            # keep each beat's offset inside the segment, shifted with it;
            # first-beat lead-in is preserved via its original offset
            b["start"] = round(b["start"] + delta, 3) if prev_end is None else round(prev_end + gap, 3)
            a = os.path.join(AUDIO_DIR, b.get("file") or f"beat_{b['index']:03d}.mp3")
            dur = rs.ffprobe_dur(a) if os.path.exists(a) else round(b["end"] + delta - b["start"], 3)
            b["end"] = round(b["start"] + dur, 3)
            prev_end = b["end"]
        occupied = round((prev_end - seg["start"]), 3) if prev_end is not None else 0.0
        if not seg.get("dur") or seg["dur"] < occupied:
            seg["dur"] = round(occupied + (GAP_SEC if occupied else 2.5), 3)
        seg["end"] = round(seg["start"] + seg["dur"], 3)
        t = seg["end"]


class NarrationIn(BaseModel):
    beats: list   # [{"index": int, "text": str}, ...]


@app.post("/api/segments/{seg_index}/narration")
def edit_narration(seg_index: int, body: NarrationIn):
    import storyboard_edit
    segs = load_segments()
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    if not seg:
        raise HTTPException(404, "segment not found")
    _snapshot()
    edited = {e["index"]: e["text"].strip() for e in body.beats}
    # If any edited beat was SLICED across segments by a storyboard boundary
    # move, merge it back into one whole beat first — an edit always re-TTSes
    # the full sentence, and the re-slice (if wanted) is a new boundary move.
    for bi in list(edited):
        parts = [b for s in segs for b in s["beats"]
                 if b["index"] == bi and b.get("file")]
        if parts:
            storyboard_edit.coalesce_beat(active_project_dir(), bi)
    segs = load_segments()
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    owner = seg if seg and any(b["index"] in edited for b in seg["beats"]) else \
        next((s for s in segs if any(b["index"] in edited for b in s["beats"])), None)
    if owner is None:
        raise HTTPException(404, "edited beat not found")
    for b in owner["beats"]:
        if b["index"] in edited and edited[b["index"]] and edited[b["index"]] != b["text"]:  # noqa: E501
            b["text"] = edited[b["index"]]
            b.pop("file", None)
            try:
                _synth_rest(b["text"],
                            os.path.join(AUDIO_DIR, f"beat_{b['index']:03d}.mp3"))
            except Exception as e:
                raise HTTPException(500, f"TTS failed: {e}")
    _recompute_timeline(segs)          # new audio length ripples downstream
    _write_segments(segs)
    # the clip on disk still carries the OLD narration; finalize only renders
    # clips that are MISSING, so an edited-but-existing clip would ship stale.
    import storyboard_edit
    storyboard_edit._stale(active_project_dir(), [owner["seg_index"]])
    job = _start_render([owner["seg_index"]])
    return {"ok": True, "seg_index": owner["seg_index"], "dur": owner["dur"], "job": job}


# --- structural edits: reorder / split / merge --------------------------
# seg_index is a STABLE id (clips are named seg_<id>.mp4), so reordering never
# invalidates a clip — only the LIST ORDER (= play/concat order) changes.

def _clip_rel(seg):
    return f"clips/seg_{seg['seg_index']:03d}.mp4"


def _new_id(segs):
    return max((s["seg_index"] for s in segs), default=-1) + 1


class ReorderIn(BaseModel):
    order: list   # list of seg_index in the desired play order


@app.post("/api/segments/reorder")
def reorder(body: ReorderIn):
    segs = load_segments()
    by_id = {s["seg_index"]: s for s in segs}
    if sorted(body.order) != sorted(by_id):
        raise HTTPException(400, "order must be a permutation of all seg_index")
    _snapshot()
    segs = [by_id[i] for i in body.order]
    _recompute_timeline(segs)          # play order changed -> retime
    _write_segments(segs)              # clips unchanged; export re-concats in new order
    return {"ok": True, "order": body.order}


class SplitIn(BaseModel):
    after: int    # split this segment after its Nth beat (0-based, within segment)


@app.post("/api/segments/{seg_index}/split")
def split_segment(seg_index: int, body: SplitIn):
    segs = load_segments()
    pos = next((i for i, s in enumerate(segs) if s["seg_index"] == seg_index), None)
    if pos is None:
        raise HTTPException(404, "segment not found")
    seg = segs[pos]
    if not (0 <= body.after < len(seg["beats"]) - 1):
        raise HTTPException(400, "split point must leave a beat on each side")
    _snapshot()
    head_beats = seg["beats"][:body.after + 1]
    tail_beats = seg["beats"][body.after + 1:]
    seg["beats"] = head_beats
    tail = {
        "seg_index": _new_id(segs),
        "panel_id": seg["panel_id"], "panel_file": seg.get("panel_file"),
        "beats": tail_beats, "start": 0.0, "end": 0.0, "dur": 0.0,
    }
    tail["clip"] = _clip_rel(tail)
    segs.insert(pos + 1, tail)
    _recompute_timeline(segs)
    _write_segments(segs)
    job = _start_render([seg["seg_index"], tail["seg_index"]])  # both halves, background
    return {"ok": True, "new_seg_index": tail["seg_index"], "job": job}


class MergeIn(BaseModel):
    a: int        # keep this segment (its panel), absorb b's beats
    b: int        # must be the segment immediately after a in play order


@app.post("/api/segments/merge")
def merge_segments(body: MergeIn):
    segs = load_segments()
    ia = next((i for i, s in enumerate(segs) if s["seg_index"] == body.a), None)
    ib = next((i for i, s in enumerate(segs) if s["seg_index"] == body.b), None)
    if ia is None or ib is None:
        raise HTTPException(404, "segment not found")
    if ib != ia + 1:
        raise HTTPException(400, "can only merge two adjacent segments (b right after a)")
    _snapshot()
    a, b = segs[ia], segs[ib]
    a["beats"] = a["beats"] + b["beats"]   # a's panel spans all beats now
    segs.pop(ib)
    _recompute_timeline(segs)
    _write_segments(segs)
    job = _start_render([a["seg_index"]])   # merged clip re-renders in background
    return {"ok": True, "seg_index": a["seg_index"], "job": job}


@app.post("/api/undo")
def undo():
    files = sorted(f for f in os.listdir(VERSIONS) if f.endswith(".json"))
    if not files:
        raise HTTPException(400, "nothing to undo")
    last = os.path.join(VERSIONS, files[-1])
    with open(last) as f:
        segs = json.load(f)
    _write_segments(segs)
    os.remove(last)
    # re-render every segment whose panel differs from what's now on disk is
    # overkill; just re-render the ones that changed is unknown here, so the
    # caller reloads and can re-render as needed. Return restored state.
    return {"ok": True, "restored": files[-1], "n_segments": len(segs)}


# static SPA last so /api and /clip take precedence
# ======================================================================
#  LIVE IN-BROWSER PREVIEW  — play the composition HTML directly, so edits
#  preview INSTANTLY (no render). Same card-over-blurred-blowup + Ken Burns
#  look as the MP4, driven by one JS clock that switches the active segment
#  and syncs each beat's audio. MP4 render becomes export-only.
# ======================================================================

@app.get("/audio/{beat_index}")
def audio(beat_index: int):
    path = os.path.join(AUDIO_DIR, f"beat_{beat_index:03d}.mp3")
    if not os.path.exists(path):
        raise HTTPException(404, "audio not found")
    return FileResponse(path, media_type="audio/mpeg")


def build_preview_html(segs):
    """Self-contained full-timeline composition that PLAYS in the browser."""
    import html as _html
    total = round(sum(s["dur"] for s in segs), 3)
    layers, meta, audios = [], [], []
    tcur = 0.0
    for i, s in enumerate(segs):
        pid = s["panel_id"]
        img = f"/panelimg/{_html.escape(pid)}"
        dur = s["dur"]
        has_crop = s.get("crop_bbox_norm") is not None
        if has_crop:
            import sys
            if RECAP not in sys.path:
                sys.path.insert(0, RECAP)
            from shot_planner import get_crop_layout
            layout = get_crop_layout(s["crop_bbox_norm"], s.get("width"), s.get("height"))
            layers.append(
                f'<div class="seg" id="seg{i}" style="opacity:0">'
                f'<img class="bg" src="{img}" alt="">'
                f'<div class="veil"></div>'
                f'<div class="card">'
                f'<div class="crop-container" id="ci{i}" style="position:relative; overflow:hidden; width:{layout["w"]:.1f}px; height:{layout["h"]:.1f}px; border-radius:6px; background:#fff; box-shadow:0 30px 70px rgba(0,0,0,.38), 0 8px 20px rgba(0,0,0,.22);">'
                f'<img class="cardimg" src="{img}" style="position:absolute; width:{layout["scale_w"]}%; height:{layout["scale_h"]}%; left:{layout["left"]}%; top:{layout["top"]}%; max-width:none; max-height:none;" alt="">'
                f'</div>'
                f'</div>'
                f'</div>')
        else:
            layers.append(
                f'<div class="seg" id="seg{i}" style="opacity:0">'
                f'<img class="bg" src="{img}" alt="">'
                f'<div class="veil"></div>'
                f'<div class="card"><img class="cardimg" id="ci{i}" src="{img}" alt=""></div>'
                f'</div>')
        meta.append({"start": round(tcur, 3), "dur": dur,
                     "even": i % 2 == 0, "text": s["beats"][0]["text"] if s["beats"] else ""})
        for b in s["beats"]:
            a = os.path.join(AUDIO_DIR, f"beat_{b['index']:03d}.mp3")
            if os.path.exists(a):
                import render_segments as rs
                audios.append({"id": f"a{b['index']}", "idx": b["index"],
                               "start": round(b["start"], 3),
                               "dur": rs.ffprobe_dur(a)})
        tcur += dur
    audio_els = "\n".join(
        f'<audio id="{a["id"]}" src="/audio/{a["idx"]}" preload="auto"></audio>'
        for a in audios)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:100%;height:100%;background:#0b0c10;overflow:hidden;font-family:system-ui}}
#stage{{position:absolute;inset:0;bottom:44px;background:#e8e6e3;overflow:hidden}}
.seg{{position:absolute;inset:0;transition:opacity .18s}}
.bg{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  filter:blur(42px) saturate(.5) brightness(1.08);transform:scale(1.18)}}
.veil{{position:absolute;inset:0;background:radial-gradient(ellipse at center,
  rgba(232,230,227,.25) 0%,rgba(232,230,227,.72) 100%)}}
.card{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center}}
.cardimg{{max-width:46%;max-height:90%;object-fit:contain;border-radius:6px;background:#fff;
  box-shadow:0 30px 70px rgba(0,0,0,.38),0 8px 20px rgba(0,0,0,.22)}}
#bar{{position:absolute;left:0;right:0;bottom:0;height:44px;background:#15161c;
  display:flex;align-items:center;gap:10px;padding:0 12px;color:#cfd2dc;font-size:13px}}
#play{{cursor:pointer;background:#5b8cff;border:0;color:#fff;border-radius:6px;padding:6px 12px}}
#seek{{flex:1}} #tlabel{{font-variant-numeric:tabular-nums;min-width:96px}}
#cap{{position:absolute;left:0;right:0;bottom:52px;text-align:center;color:#111;
  font-size:14px;padding:0 20px;pointer-events:none}}
</style></head><body>
<div id="stage">{''.join(layers)}</div>
<div id="cap"></div>
<div id="bar"><button id="play">▶</button>
  <input id="seek" type="range" min="0" max="1000" value="0">
  <span id="tlabel">0.0 / {total:.1f}s</span></div>
{audio_els}
<script>
const SEGS={json.dumps(meta)}, AUD={json.dumps(audios)}, TOTAL={total};
let t=0, playing=false, t0=0, raf=null;
const $=id=>document.getElementById(id);
function fmt(x){{return x.toFixed(1)}}
function render(t){{
  let cap="";
  SEGS.forEach((s,i)=>{{
    const on = t>=s.start && t<s.start+s.dur;
    const el=$("seg"+i); el.style.opacity=on?1:0;
    if(on){{ const p=(t-s.start)/s.dur; const z=s.even?1+0.035*p:1.035-0.035*p;
      $("ci"+i).style.transform="scale("+z.toFixed(4)+")"; cap=s.text; }}
  }});
  $("cap").textContent=cap;
  // Audio plays ONLY while actually playing — never on open (render(0)),
  // never on scrub/seek, never on a timeline jump unless already playing.
  // (Previously render() started the first clip at t=0 on load = "autoplay".)
  AUD.forEach(a=>{{ const el=$(a.id); const on = playing && t>=a.start && t<a.start+a.dur;
    if(on){{ if(el.paused){{ try{{el.currentTime=Math.max(0,t-a.start)}}catch(e){{}} el.play().catch(()=>{{}}); }} }}
    else if(!el.paused) el.pause(); }});
  $("seek").value=Math.round(t/TOTAL*1000);
  $("tlabel").textContent=fmt(t)+" / "+fmt(TOTAL)+"s";
  try{{ parent.postMessage({{type:"pv-time", t:t, total:TOTAL, playing:playing}}, "*"); }}catch(e){{}}
}}
// let the parent (CapCut timeline) drive seek/playpause
window.addEventListener("message", e=>{{
  const m=e.data||{{}};
  if(m.type==="pv-seek"){{ pause(); t=Math.max(0,Math.min(TOTAL,m.t)); t0=performance.now()-t*1000; render(t); }}
  else if(m.type==="pv-playpause"){{ playing?pause():play(); }}
}});
function loop(now){{ t=(now-t0)/1000; if(t>=TOTAL){{t=TOTAL;pause();}} render(t); if(playing) raf=requestAnimationFrame(loop); }}
function play(){{ if(t>=TOTAL) t=0; playing=true; $("play").textContent="⏸"; t0=performance.now()-t*1000; raf=requestAnimationFrame(loop); }}
function pause(){{ playing=false; $("play").textContent="▶"; cancelAnimationFrame(raf); AUD.forEach(a=>{{const el=$(a.id); if(!el.paused)el.pause();}}); }}
$("play").onclick=()=>playing?pause():play();
$("seek").oninput=e=>{{ pause(); t=e.target.value/1000*TOTAL; t0=performance.now()-t*1000; render(t); }};
render(0);
</script></body></html>"""


@app.get("/api/preview")
def preview():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(build_preview_html(load_segments()))


# ======================================================================
#  NON-BLOCKING RE-RENDER  — edits return a job id immediately; the clip
#  re-renders in a background thread; the UI polls /api/jobs/{id}. The live
#  preview already reflects the edit instantly, so nothing blocks on render.
# ======================================================================
import threading
import uuid

JOBS = {}   # job_id -> {status: queued|running|done|error, done, total, error}


def _jobs_dir():
    import ingest as _ing
    d = os.path.join(_ing.PROJECTS, "_jobs")
    os.makedirs(d, exist_ok=True)
    return d


def _persist_job(job_id):
    """Mirror a render/export job record to disk so the Logs drawer and
    status polling survive container restarts (same guarantee ingest has)."""
    try:
        rec = dict(JOBS.get(job_id) or {})
        rec["job"] = job_id
        # A heartbeat on every write is what makes "is this job alive?"
        # answerable. Without it a worker killed mid-run leaves a record that
        # says "running" forever and nothing can tell it from real progress.
        rec["heartbeat"] = time.time()
        rec.setdefault("ts", rec["heartbeat"])
        JOBS.get(job_id, {}).update(
            {"heartbeat": rec["heartbeat"], "ts": rec["ts"]})
        tmp = os.path.join(_jobs_dir(), f"render_{job_id}.json.tmp")
        with open(tmp, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, os.path.join(_jobs_dir(), f"render_{job_id}.json"))
    except Exception:
        pass


# Clips rendered at once by the finalize and render jobs. Each clip is an
# independent mp4, so they parallelise cleanly; the box is 32 vCPU / 32 GB and
# a single clip peaked at ~5 vCPU (render_segments.py profile notes). Rollback
# without a deploy: RENDER_CLIP_PARALLEL=1 restores one-at-a-time.
def _clip_parallel():
    try:
        return max(1, min(8, int(os.environ.get("RENDER_CLIP_PARALLEL", 3))))
    except ValueError:
        return 3


def _render_clips(seg_indices, on_start, on_done, control=None):
    """Render clips up to _clip_parallel() at a time, in order.

    Everything that used to happen between clips still happens before EACH
    clip is handed out: the pause/stop gate, a fresh read of segments.json,
    and the per-clip timeline gate. The first failure stops new work; clips
    already rendering finish (their mp4s are valid), then the failure is
    raised — so a stop or a broken timeline never strands a half clip.
    """
    import concurrent.futures as cf
    n = _clip_parallel()
    pending, running, error = list(seg_indices), {}, None
    work = os.path.join(active_project_dir(), ".render_work")
    with cf.ThreadPoolExecutor(max_workers=n) as ex:
        while pending or running:
            while pending and len(running) < n and error is None:
                try:
                    if control:
                        control()
                    si = pending[0]
                    seg = next((x for x in load_segments() if x["seg_index"] == si), None)
                    if seg is not None:
                        _gate_timeline([si], "render")
                except BaseException as e:   # stop / pause-then-stop / gate
                    error = e
                    break
                pending.pop(0)
                if seg is None:
                    on_done(si)
                    continue
                on_start(si)
                wd = os.path.join(work, f"seg_{si:03d}") if n > 1 else None
                running[ex.submit(_rerender, seg, wd)] = si
            if not running:
                break
            done, _ = cf.wait(running, return_when=cf.FIRST_COMPLETED)
            for f in done:
                si = running.pop(f)
                if f.exception() is not None:
                    error = error or f.exception()
                else:
                    on_done(si)
    if error is not None:
        raise error


_FINALIZE_LOCK = threading.Lock()


def _run_finalize_job(job_id):
    """The APPROVE chain (user contract): render every ticked-but-missing
    clip, then export the final narrated MP4 — one job, visible progress."""
    with _FINALIZE_LOCK:
        _do_run_finalize_job(job_id)


def _do_run_finalize_job(job_id):
    import time
    usage.set_job(job_id)          # a re-voice counts under this render, not "unknown"
    j = JOBS[job_id]
    j["status"] = "running"
    j["stage"] = "render"
    _persist_job(job_id)
    _fname = _pretty(j.get("project") or "")
    _ev("render", f"{_fname} approved → render + export")
    try:
        pdir = active_project_dir()
        segs = load_segments()
        ticked = video_segments(segs)
        missing = needs_render(ticked, pdir)
        # Same pre-render gate as /api/render-missing, over every ticked seg
        # (the export gates all of them anyway). Without it the job died on
        # the renderer's own check at the first bad clip, after spending
        # render time on the ones before it, and named only that one fault.
        _gate_timeline([s["seg_index"] for s in ticked], "render")
        j["total"] = len(missing)
        j["done"] = 0

        def started(si):
            j["current_seg"] = si
            _persist_job(job_id)

        def finished(si):
            j["done"] += 1
            _persist_job(job_id)

        # Owner, 2026-10-04: approving applies the CURRENT studio voice. A
        # chapter voiced before the voice/style was changed is re-voiced first
        # (cached lines cost nothing), re-timed, and its clips re-rendered.
        nv = 0 if j.get("keep_voice") else _revoice_if_outdated(pdir, j, job_id)
        if nv:
            _ev("render", f"{_fname}: re-voiced {nv} lines in the studio voice")
            segs = load_segments()
            ticked = video_segments(segs)
            missing = needs_render(ticked, pdir)
            j["stage"] = "render"
            j["total"] = len(missing)
            j["done"] = 0
            _persist_job(job_id)
        # N clips at a time; each is gated against the timeline as it is
        # when its turn comes, and nothing is written back (358-lab-claude
        # seg 66: the old loop re-saved a stale copy after every clip).
        _render_clips(missing, started, finished)
        j["stage"] = "export"
        j["current_seg"] = None
        _persist_job(job_id)
        speed = _studio.export_speed()
        res = _do_export(speed)
        j["export"] = res.get("output")
        j["url"] = res.get("url")
        j["status"] = "done"
        j["ended"] = time.time()
        _ld = res.get("loudness") or {}
        j["loudness"] = _ld
        _ev("render", f"{_fname} exported → {res.get('output')} ({speed}x"
                      + (f", loudness {_ld['before']} → {_ld['target']:g} LUFS" if _ld.get("applied") else
                         f", loudness unchanged: {_ld.get('why')}" if _ld else "") + ")", "ok")
        threading.Thread(target=_after_export, args=(pdir, res.get("output")), daemon=True).start()
    except HTTPException as e:
        j["status"] = "error"
        j["error"] = str(e.detail)
    except Exception as e:  # noqa
        j["status"] = "error"
        j["error"] = str(e)
    if j.get("status") == "error":
        j["ended"] = time.time()
        _ev("render", f"{_fname} render failed: {str(j.get('error'))[:160]}", "error")
    _persist_job(job_id)


def _voice_outdated(pdir):
    """The studio default voice record, if this chapter was voiced in a
    different voice or style; else None."""
    import gemini_tts
    import ingest as _ing
    want = gemini_tts.load_default(_ing.PROJECTS)
    if not want:
        return None
    try:
        have = gemini_tts.engine_for_project(pdir)
    except Exception:
        return None
    key = lambda r: (r.get("provider"), r.get("voice"), (r.get("style") or "").strip())
    return want if key(want) != key(have) else None


def _voice_label(rec):
    if not rec:
        return "unknown voice"
    if rec.get("provider") == "chirp":
        return "legacy Chirp voice"
    st = (rec.get("style") or "").strip()
    return f"{rec.get('voice')}, " + (f"style “{st[:40]}{'…' if len(st) > 40 else ''}”" if st else "no style")


def _revoice_plan(pdir):
    """What approving would re-record, and what it would cost, before any
    file is touched. None when the chapter is already in the studio voice."""
    import gemini_tts
    want = _voice_outdated(pdir)
    if not want:
        return None
    try:
        have = gemini_tts.engine_for_project(pdir)
    except Exception:
        have = None
    try:
        with open(os.path.join(pdir, "segments.json"), encoding="utf-8") as f:
            segs = json.load(f)
    except (OSError, ValueError):
        segs = []
    texts = [b.get("text") or "" for s in segs for b in s.get("beats", []) if (b.get("text") or "").strip()]
    model = want.get("model") or ""
    est = sum(usage._est_cost("tts", len(t), model) for t in texts)
    spent = float(usage.daily_summary().get("est_cost_usd") or 0.0)
    cap = float(usage.daily_cap())
    return {"from": _voice_label(have), "to": _voice_label(want), "lines": len(texts),
            "est_usd": round(est, 3), "spent_today": round(spent, 3), "cap": cap,
            "fits": spent + est <= cap}


def _revoice_engine(want):
    import gemini_tts
    if want.get("provider") == "chirp":
        key = gemini_tts.env_any_case("TTS_API_KEY")
        if not key:
            raise RuntimeError("TTS_API_KEY is not set")
        return gemini_tts._chirp_cfg(key)
    key = gemini_tts.env_any_case("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return {"provider": "gemini", "api_key": key, "model": want.get("model") or gemini_tts.DEFAULT_MODEL,
            "voice": want.get("voice") or gemini_tts.DEFAULT_VOICE, "style": want.get("style", "")}


def _revoice_if_outdated(pdir, j, job_id):
    """Re-record every line in the studio voice, re-time, mark clips stale.
    Returns the number of lines re-voiced (0 = already current).

    All-or-nothing (owner, 2026-10-05: ch.30 failed on the daily cap after the
    pin and timeline had already been rewritten): the budget is checked first,
    new lines are recorded to a side folder, and only when every line is in
    are the pin, the audio and the timeline swapped in. A failure leaves the
    chapter exactly as it was, still in its own voice."""
    import shutil
    import gemini_tts
    import storyboard_edit
    want = _voice_outdated(pdir)
    if not want:
        return 0
    plan = _revoice_plan(pdir) or {}
    if not plan.get("fits", True):
        left = max(0.0, plan["cap"] - plan["spent_today"])
        raise RuntimeError(
            f"the studio voice changed ({plan['from']} → {plan['to']}), so all {plan['lines']} lines "
            f"need re-recording, about ${plan['est_usd']:.2f} — but only ${left:.2f} of today's "
            f"${plan['cap']:.0f} budget is left. Nothing was changed. Render with the chapter's own "
            f"voice (free), or try again after midnight ET.")
    eng = _revoice_engine(want)
    seg_path = os.path.join(pdir, "segments.json")
    with open(seg_path, "rb") as f:
        seg_backup = f.read()
    try:
        segs = load_segments()
        for bi in sorted({b["index"] for s in segs for b in s["beats"] if b.get("file")}):
            storyboard_edit.coalesce_beat(pdir, bi)       # sliced lines are re-recorded whole
        segs = load_segments()
        # Which segment lengths were automatic (old narration + the standard gap)?
        # Those re-fit the new audio; a length set by hand is kept. Without this a
        # faster voice would leave dead air in every segment (the visual track is
        # authoritative in _recompute_timeline and only ever grows).
        auto_fit = set()
        for sg in segs:
            if sg["beats"]:
                occ = max(b["end"] for b in sg["beats"]) - sg.get("start", 0)
                if abs(sg.get("dur", 0) - (occ + GAP_SEC)) <= 0.6:
                    auto_fit.add(sg["seg_index"])
        beats = [b for s in segs for b in s["beats"] if (b.get("text") or "").strip()]
        j.update(stage="revoice", total=len(beats), done=0,
                 note=f"re-recording in the studio voice ({_voice_label(want)})")
        _persist_job(job_id)
        adir = os.path.join(pdir, "audio")
        side = os.path.join(adir, ".revoice")
        shutil.rmtree(side, ignore_errors=True)
        os.makedirs(side, exist_ok=True)

        def one(b):
            if j.get("control") == "stop":
                raise RuntimeError("stopped by you while re-voicing")
            _synth_rest(b["text"], os.path.join(side, f"beat_{b['index']:03d}.mp3"), engine=eng)
            j["done"] = j.get("done", 0) + 1

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(usage.carry(one), beats))
    except Exception:
        with open(seg_path, "wb") as f:                 # back to how it was
            f.write(seg_backup)
        shutil.rmtree(os.path.join(pdir, "audio", ".revoice"), ignore_errors=True)
        raise
    # Every line is in: swap it all at once.
    for b in beats:
        b.pop("file", None)
        os.replace(os.path.join(side, f"beat_{b['index']:03d}.mp3"),
                   os.path.join(adir, f"beat_{b['index']:03d}.mp3"))
    shutil.rmtree(side, ignore_errors=True)
    vid = "chirp:Charon" if want.get("provider") == "chirp" else "gemini:" + str(want.get("voice"))
    gemini_tts._write_json(os.path.join(pdir, gemini_tts.PIN_FILE),
                           gemini_tts.parse_choice(vid, want.get("style")))
    for sg in segs:
        if sg["seg_index"] in auto_fit:
            sg["dur"] = 0              # re-fit to the new narration
    _recompute_timeline(segs)
    _write_segments(segs)
    storyboard_edit._stale(pdir, [s["seg_index"] for s in segs])
    _persist_job(job_id)
    return len(beats)


def _run_render_job(job_id, seg_indices):
    JOBS[job_id]["status"] = "running"
    try:
        # Refuse up front with every timing fault, not mid-batch with one.
        _gate_timeline(seg_indices, "render")
        def finished(si):
            JOBS[job_id]["done"] += 1
        # pause/stop + a fresh per-clip timeline gate before each clip;
        # N at a time; no stale write-back.
        _render_clips(seg_indices, lambda si: None, finished,
                      control=lambda: _control_gate(JOBS, job_id, _persist_job))
        JOBS[job_id]["status"] = "done"
    except JobCancelled as e:
        JOBS[job_id]["status"] = "cancelled"
        JOBS[job_id]["error"] = str(e)
        _persist_job(job_id)
    except HTTPException as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e.detail)
    except Exception as e:  # noqa
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)


def _active_render_for_project(project):
    """A render already in flight for this project, if any.

    Ingest has had this guard since the folder-race bug; render never did, so
    a second render could be started on top of a first and both would write
    the same clips directory. That is how one project ended up with a 9-hour
    zombie and a second job stuck at 0 of 59.
    """
    _sweep_stalled_jobs()
    for jid, j in JOBS.items():
        if (j.get("project") == project
                and j.get("status") in ("queued", "running")):
            return jid
    return None


def _start_render(seg_indices):
    project = os.path.basename(active_project_dir().rstrip("/"))
    existing = _active_render_for_project(project)
    if existing:
        raise HTTPException(
            409, f"a render is already running for {project} (job {existing}). "
                 f"Wait for it, or stop it first — starting a second render "
                 f"writes the same clips and neither finishes cleanly.")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "done": 0, "total": len(seg_indices),
                    "seg_indices": seg_indices, "error": None,
                    "project": project, "ts": time.time(),
                    "heartbeat": time.time()}
    threading.Thread(target=_run_render_job, args=(job_id, seg_indices),
                     daemon=True).start()
    return job_id


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    j = JOBS.get(job_id)
    if not j:  # restart-proof: fall back to the persisted record
        p = os.path.join(_jobs_dir(), f"render_{job_id}.json")
        if os.path.exists(p):
            return json.load(open(p))
        raise HTTPException(404, "job not found")
    return j


STALL_SECONDS = float(os.environ.get("JOB_STALL_SECONDS", 1800))
STOP_GRACE_SECONDS = float(os.environ.get("JOB_STOP_GRACE_SECONDS", 120))


def _sweep_stalled_jobs():
    """Retire jobs whose worker is gone, WITHOUT waiting for a restart.

    Observed 2026-09-22: two renders on the-extras-academy-survival-guide were
    still marked "running" — one for 9.2 hours after the operator pressed STOP
    (control="stop", never acknowledged), and a second at 0 of 59 clips for
    1.9 hours. Nothing could retire them, because the only sweep ran at boot
    and no deploy had happened since.

    Two rules:
      - control="stop" is honoured cooperatively by a LIVE worker. If the
        worker has not checked in within STOP_GRACE_SECONDS, it is not alive,
        so the stop is applied here instead of hanging forever.
      - any running job silent for STALL_SECONDS is declared dead.

    Runs on every job listing, so the system heals itself while the operator
    is looking at it rather than at the next deploy.
    """
    now = time.time()
    swept = []
    d = _jobs_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return swept
    for fn in names:
        if not fn.endswith(".json"):
            continue
        fp = os.path.join(d, fn)
        try:
            rec = json.load(open(fp))
        except Exception:
            continue
        if rec.get("status") not in ("running", "queued"):
            continue
        last = rec.get("heartbeat") or rec.get("ts") or 0
        age = now - last
        stopping = rec.get("control") == "stop"
        if stopping and age > STOP_GRACE_SECONDS:
            rec["status"] = "cancelled"
            rec["error"] = ("stopped by the operator; the worker did not "
                            "acknowledge, so the job was retired here")
        elif age > STALL_SECONDS:
            rec["status"] = "error"
            rec["error"] = (f"no progress for {age/60:.0f} minutes — the worker "
                            f"is gone (restart, crash, or killed mid-run). "
                            f"Cached work is kept; re-submit to resume.")
        else:
            continue
        jid = rec.get("job") or fn[:-5].replace("render_", "")
        try:
            json.dump(rec, open(fp, "w"))
        except Exception:
            continue
        if jid in JOBS:
            JOBS[jid].update({"status": rec["status"], "error": rec["error"]})
        swept.append(jid)
    if swept:
        print(f"[sweep] retired {len(swept)} dead job(s): {swept}", flush=True)
    return swept


@app.get("/api/jobs")
def jobs_recent(limit: int = 20):
    """Recent render/export job records (persisted; restart-proof) for the
    Logs drawer — newest first."""
    _sweep_stalled_jobs()
    recs = []
    d = _jobs_dir()
    for f in os.listdir(d):
        if f.startswith("render_") and f.endswith(".json"):
            fp = os.path.join(d, f)
            try:
                rec = json.load(open(fp))
            except Exception:
                continue
            # Fall back to the file's own mtime when the record carries no ts.
            # Sorting a missing timestamp as 0 buried every lab run at the
            # bottom of the list, where `limit` then cut it off entirely.
            if not rec.get("ts"):
                try:
                    rec["ts"] = os.path.getmtime(fp)
                    rec["ts_inferred"] = True
                except OSError:
                    rec["ts"] = 0
            recs.append(rec)
    recs.sort(key=lambda r: r.get("ts") or 0, reverse=True)
    return {"jobs": recs[:limit]}


def _project_label(project_dir):
    """Series + chapter for a project, read from its own project.json.

    An export is identified by WHAT IT IS — "Overgeared Ch.339" — not by the
    slug its folder happens to have or the timestamp it happened to be written
    at. The drawer used to lead with the filename, which is the one string that
    tells you least about which manhwa you are looking at.
    """
    try:
        with open(os.path.join(project_dir, "project.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"series": "", "chapter": "", "title": "", "part": ""}
    series = (meta.get("series") or "").strip()
    chapter = str(meta.get("chapter") or "").strip()
    title = series + (f" Ch.{chapter}" if chapter else "")
    return {"series": series, "chapter": chapter,
            "title": title or os.path.basename(project_dir.rstrip("/")),
            "part": str(meta.get("part") or "").strip()}


@app.get("/api/exports")
def list_exports():
    """Every exported MP4 for the ACTIVE project: series/chapter, name, size,
    created (ET), duration — the Exports drawer's data source."""
    import subprocess as sp
    from datetime import datetime
    from zoneinfo import ZoneInfo
    prune_exports()
    active_pid = os.path.basename(active_project_dir().rstrip("/"))
    out = []
    for pid, d in _all_export_dirs():
        label = _project_label(os.path.dirname(d))
        cache_p = os.path.join(d, ".durations.json")
        try:
            cache = json.load(open(cache_p))
        except Exception:
            cache = {}
        for f in sorted(os.listdir(d)):
            if not f.endswith(".mp4"):
                continue
            p = os.path.join(d, f)
            st = os.stat(p)
            key = f + str(int(st.st_mtime))
            if key not in cache:
                try:
                    cache[key] = round(float(sp.check_output(
                        ["ffprobe", "-v", "error", "-show_entries",
                         "format=duration", "-of", "csv=p=0", p],
                        text=True).strip()), 1)
                except Exception:
                    cache[key] = None
            age_d = (time.time() - st.st_mtime) / 86400.0
            try:                       # Phase A: review verdict per export
                _rs = review_state(os.path.dirname(d), f)
            except Exception:
                _rs = {"status": "review_pending", "superseded": False}
            out.append({
                "name": f, "project": pid, "size_mb": round(st.st_size / 1e6, 1),
                "series": label["series"], "chapter": label["chapter"],
                "title": label["title"], "part": label["part"],
                "review_status": _rs.get("status"),
                "superseded": _rs.get("superseded"),
                "review_url": f"/review?project={pid}&name={f}",
                "duration": cache[key],
                "created": datetime.fromtimestamp(
                    st.st_mtime, ZoneInfo("America/New_York")
                ).strftime("%b %d %I:%M %p ET"),
                "mtime": st.st_mtime,
                "age_days": round(age_d, 2),
                "expires_in_days": max(0, round(EXPORT_RETENTION_DAYS - age_d, 2)),
                "active_project": pid == active_pid,
                "url": f"/export/{f}?project={pid}"})
        try:
            json.dump(cache, open(cache_p, "w"))
        except Exception:
            pass
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return {"exports": out, "retention_days": EXPORT_RETENTION_DAYS}


# ======================================================================
#  CHAPTER-URL INGESTION  — drop a chapter link, run the whole pipeline
#  (scrape→split→describe→narrate→voice→match→segment) as a background job
#  with live stage progress, then activate it in the studio.
# ======================================================================
INGEST = {}   # job_id -> {stage, pct, msg, status, error, project, url, ts}
# Ingest job status is ALSO persisted to a small JSON file per job on the
# volume, so the Logs tab and status polling survive a container restart
# (in-memory INGEST is lost on restart; the files are not).
import ingest as _ingest_mod
_JOBS_DIR = os.path.join(_ingest_mod.PROJECTS, "_jobs")
os.makedirs(_JOBS_DIR, exist_ok=True)


def _persist_ingest(job_id):
    try:
        INGEST[job_id]["updated"] = time.time()
        with open(os.path.join(_JOBS_DIR, f"{job_id}.json"), "w") as f:
            json.dump(INGEST[job_id], f)
    except Exception:
        pass


class JobCancelled(Exception):
    """Raised inside a worker when the operator pressed stop."""


# Jobs are cooperative, not preemptive: a worker only notices pause/stop when
# it next reports progress. For an ingest that is between pipeline steps; for a
# render it is between clips. So a stop lands within seconds, not instantly —
# an in-flight Gemini call or ffmpeg render finishes first. Anything stronger
# would mean killing the process, which is exactly the restart that has cost
# this project two paid ingests.
_PAUSE_POLL = 0.5
_PAUSE_MAX = 3600.0


def _control_gate(store, job_id, persist=None):
    """Honour pause/stop for a running job. Raises JobCancelled on stop."""
    rec = store.get(job_id)
    if not rec:
        return
    if rec.get("control") == "stop":
        raise JobCancelled("stopped by user")
    waited = 0.0
    while rec.get("control") == "pause" and waited < _PAUSE_MAX:
        if rec.get("status") != "paused":
            rec["status"] = "paused"
            if persist:
                persist(job_id)
        time.sleep(_PAUSE_POLL)
        waited += _PAUSE_POLL
        if rec.get("control") == "stop":
            raise JobCancelled("stopped by user")
    if rec.get("status") == "paused":
        rec["status"] = "running"
        if persist:
            persist(job_id)


def _load_ingest(job_id):
    """In-memory first, else the durable file (survives restart)."""
    if job_id in INGEST:
        return INGEST[job_id]
    p = os.path.join(_JOBS_DIR, f"{job_id}.json")
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return None
    return None


def _all_ingest_jobs():
    """Every ingest job (in-memory + persisted), newest first."""
    jobs = {}
    if os.path.isdir(_JOBS_DIR):
        for f in os.listdir(_JOBS_DIR):
            if f.endswith(".json"):
                try:
                    j = json.load(open(os.path.join(_JOBS_DIR, f)))
                    jobs[f[:-5]] = j
                except Exception:
                    pass
    jobs.update(INGEST)  # in-memory is freshest
    out = [{"job": k, **v} for k, v in jobs.items()]
    out.sort(key=lambda j: j.get("ts", 0), reverse=True)
    return out


RESUME_MAX = 2              # restarts a single ingest may survive
RESUME_WINDOW = 6 * 3600    # older orphans are written off as before


def _sweep_orphaned_ingest_jobs():
    """E1: a server restart (deploy, env-var change, crash) kills the ingest
    thread but leaves its persisted status 'running' forever — a mystery-dead
    job (observed: da7cfaaddceb, killed mid-narrate by a redeploy, polled as
    'running' for 40+ minutes). On boot, mark every persisted running/queued
    job as aborted with an explicit reason. Cached stage artifacts survive in
    the project dir, so re-submitting the same URL resumes cheaply."""
    n = 0
    try:
        for fn in os.listdir(_JOBS_DIR):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(_JOBS_DIR, fn)
            try:
                j = json.load(open(p))
            except Exception:
                continue
            if j.get("status") in ("queued", "paused", "pausing") and j.get("control") != "stop":
                # Not running when the server stopped, so nothing was lost
                # (owner, 2026-10-06: a paused 182 and a waiting 183 were written
                # off by a deploy). Queued ones wait their turn ("held"); paused
                # ones come back paused (re-queued with control=pause).
                if j.get("status") == "queued":
                    j["status"] = "held"
                    j["msg"] = "waiting its turn after a server restart; it starts by itself"
                else:
                    j["status"] = "interrupted"
                    j["control"] = "pause"
                    j["msg"] = "paused (kept across a server restart) — press Resume to continue"
                json.dump(j, open(p, "w"))
                n += 1
                continue
            if j.get("status") == "running":
                # 2026-10-03 (owner: resume after restarts, manual ingests
                # too): a job cut off recently is marked 'interrupted' and
                # re-queued at startup with its cached stages, at most
                # RESUME_MAX times. Older or owner-stopped ones are written
                # off exactly as before.
                age = time.time() - (j.get("updated") or j.get("ts") or 0)
                if (age < RESUME_WINDOW and j.get("control") != "stop"
                        and j.get("resumes", 0) < RESUME_MAX):
                    j["status"] = "interrupted"
                    j["msg"] = "cut off by a server restart — resuming from cached stages"
                else:
                    j["status"] = "error"
                    j["error"] = ("aborted by server restart (deploy/env change) — "
                                  "re-submit the chapter URL to resume from cached stages"
                                  + (" (already resumed %d times)" % j.get("resumes", 0)
                                     if j.get("resumes", 0) >= RESUME_MAX else ""))
                json.dump(j, open(p, "w"))
                n += 1
    except FileNotFoundError:
        pass
    if n:
        print(f"[boot] marked {n} orphaned ingest job(s) as aborted", flush=True)


_sweep_orphaned_ingest_jobs()


def _active_ingest_for_url(url, variant=""):
    """Return the job_id of a running/queued job for this URL's project, if any
    — so submitting the same chapter twice reuses the one job (no double spend,
    no folder race). A saved version is a different project folder, so it is
    matched on (chapter, version), not the chapter alone."""
    pid = _ingest_mod.project_id(url, variant)
    for jid, j in INGEST.items():
        if j.get("status") in ("queued", "running") and \
                _ingest_mod.project_id(j.get("url", ""), j.get("variant", "")) == pid:
            return jid
    return None


def _ev(kind, msg, level="info", **f):
    try:
        import events
        events.emit(kind, msg, level, **f)
    except Exception:
        pass


def _pretty(url_or_pid, variant=""):
    """'murim-psychopath_44-img' / a chapter URL -> 'Murim Psychopath ch.44 (img)'."""
    import ingest as _i
    try:
        if "://" in (url_or_pid or ""):
            series, ch = _i.parse_series_chapter(url_or_pid)
        else:
            series, _, ch = (url_or_pid or "").rpartition("_")
            ch, _, variant = ch.partition("-") if not variant else (ch, "", variant)
        name = _i.to_title_case(_i.clean_series_slug(series))
        return f"{name} ch.{ch}" + (f" ({variant})" if variant else "")
    except Exception:
        return url_or_pid or "(unknown)"


def _run_ingest_job(job_id, url, fresh=False, engine="gemini", variant="",
                    direct=None):
    import ingest
    INGEST[job_id]["status"] = "running"
    _persist_ingest(job_id)
    who = "autopilot" if INGEST[job_id].get("source") == "autopilot" else "ingest"
    name = _pretty(url, variant)
    _ev(who, f"{name} → started")
    last = {"stage": None}

    def progress(stage, msg, pct):
        # the one place an ingest can be paused or stopped
        _control_gate(INGEST, job_id, _persist_ingest)
        INGEST[job_id].update(stage=stage, msg=msg, pct=pct)
        _persist_ingest(job_id)
        if stage != last["stage"]:
            last["stage"] = stage
            _ev(who, f"{name} → {stage}")
        if str(msg).startswith(("Image check", "Researching", "Series research")):
            _ev(who, f"{name}: {msg}")

    try:
        # Autopilot chapters run on Gemini's Flex tier (half price, may queue);
        # a manual ingest stays on Standard so it is not slowed down.
        tier = (os.environ.get("AUTOPILOT_TIER", "flex")
                if INGEST[job_id].get("source") == "autopilot" else "")
        meta = ingest.run_ingest(url, progress, job_id=job_id, fresh=fresh,
                                 engine=engine, variant=variant, direct=direct, tier=tier)
        INGEST[job_id].update(status="done", project=meta, pct=100)
        if isinstance(meta, dict) and meta.get("id"):     # title/description/tags while making
            threading.Thread(target=_prepare_publish, args=(project_dir_for(meta["id"]), DRAFT),
                             daemon=True).start()
    except JobCancelled as e:
        INGEST[job_id].update(status="cancelled", error=str(e))
    except subprocess.CalledProcessError as e:
        INGEST[job_id].update(status="error",
                              error=(e.stderr or str(e))[-400:])
    except usage.UsageCapExceeded as e:
        # Owner rule (2026-10-03): a cap hit PAUSES the chapter until the
        # spend day resets (midnight ET); the scheduler resumes it first.
        INGEST[job_id].update(status="budget_paused", paused_day=usage._today(),
                              error=f"paused: a usage limit was reached ({e}) — resumes after "
                                    f"midnight ET, or raise the limit in Settings → Spending and press Resume")
    except Exception as e:  # noqa
        INGEST[job_id].update(status="error", error=str(e))
    INGEST[job_id]["ended"] = time.time()
    _persist_ingest(job_id)
    _after_ingest(job_id)
    st = INGEST[job_id].get("status")
    cost = _job_cost(job_id)
    if st == "done":
        _ev(who, f"{name} done → Projects: Ready for review · ${cost:.2f}", "ok")
    elif st == "budget_paused":
        _ev(who, f"{name} paused by the spend cap — resumes after midnight ET", "warn")
    elif st == "cancelled":
        _ev(who, f"{name} stopped by you", "warn")
    else:
        _ev(who, f"{name} failed: {(INGEST[job_id].get('error') or '')[:160]}", "error")


def _job_cost(job_id):
    """Tracked AI spend of one job, from the usage call log."""
    total = 0.0
    try:
        with open(usage.LOG_PATH, encoding="utf-8") as f:
            for line in f:
                if job_id not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("job_id") == job_id:
                    total += e.get("est_cost_usd") or 0.0
    except FileNotFoundError:
        pass
    return round(total, 4)


def _after_ingest(job_id):
    """Every ingest end: keep the autopilot ledger in step, remember finished
    chapters (never re-made), and run the free story check on a new chapter.
    Never raises — bookkeeping must not turn a finished ingest into an error."""
    rec = INGEST.get(job_id) or {}
    status = rec.get("status")
    threading.Timer(2.0, lambda: (_trim_line(), _fill_line())).start()   # next chapter's turn
    try:
        import autopilot as _ap
        root = _ingest_mod.PROJECTS
        entry = _ap.on_job_end(root, job_id, status, rec.get("error"),
                               _job_cost(job_id) if status == "done" else None)
        if status == "done" and entry is None and not rec.get("variant"):
            import providers as _prov
            url = rec.get("url") or ""
            key = _prov.canonical_key(_prov.describe(url)["series_key"])
            ch = _ingest_mod.parse_series_chapter(url)[1]
            _ap.remember_done(root, _ap.ledger_key(key, ch), series_id=None,
                              chapter=ch, project=(rec.get("project") or {}).get("id"),
                              job=job_id)
    except Exception as e:  # noqa
        print(f"[autopilot] ledger update failed for {job_id}: {e}", flush=True)
    if status == "done" and rec.get("project"):
        def _check(pid=rec["project"].get("id")):
            try:
                rep = _validator.validate(os.path.join(_ingest_mod.PROJECTS, pid),
                                          review={}, mode="rules")
                INGEST[job_id]["check"] = {"findings": len(rep.get("findings") or []),
                                           "status": rep.get("status")}
                _persist_ingest(job_id)
            except Exception as e:  # noqa
                print(f"[autopilot] story check failed for {pid}: {e}", flush=True)
        threading.Thread(target=_check, daemon=True).start()


# ---------------------------------------------------------- ingest queue
# Bulk-ingesting from the Tracker must run ONE chapter at a time. Firing five
# concurrent ingests would multiply API pressure on a pipeline that already
# broke once by hitting a rate limit (Session 27 embeddings), and would race
# on the shared active-project state.
_QUEUE = []
_QUEUE_LOCK = threading.Lock()
_QUEUE_RUNNING = False


def _queue_worker():
    global _QUEUE_RUNNING
    while True:
        with _QUEUE_LOCK:
            if not _QUEUE:
                _QUEUE_RUNNING = False
                return
            job_id, url, fresh, engine, variant, direct = _QUEUE.pop(0)
        rec = INGEST.get(job_id) or {}
        if rec.get("control") == "stop" or rec.get("status") in ("cancelled", "held"):
            continue                       # dequeued before it ever started
        try:
            _run_ingest_job(job_id, url, fresh, engine=engine,
                            variant=variant, direct=direct)
        except Exception:                  # a worker crash must not kill the queue
            pass


def _enqueue_ingest(url, fresh=False, engine="gemini", variant="", direct=None,
                    source="manual", job_id=None, control="run", why=""):
    """Queue an ingest. With job_id, RE-queue that existing job (resume after a
    stop, a restart or a budget pause) so its history stays one record."""
    global _QUEUE_RUNNING
    old = _load_ingest(job_id) if job_id else None
    if old:
        rec = dict(old)
        rec.update(stage="queued", msg=why or "waiting for its turn (resumed)",
                   status="queued", error=None, control=control)
        rec.pop("ended", None)
        INGEST[job_id] = rec
    else:
        job_id = uuid.uuid4().hex[:12]
        INGEST[job_id] = {"stage": "queued", "pct": 0, "msg": "waiting for its turn",
                          "status": "queued", "error": None, "project": None,
                          "url": url, "ts": time.time(), "control": "run",
                          "engine": engine, "variant": variant, "direct_speech": direct,
                          "source": source}
    _persist_ingest(job_id)
    with _QUEUE_LOCK:
        # the engine rides with the job — it used to be dropped here, so any
        # QUEUED ingest (every Tracker ingest) silently ran Gemini
        _QUEUE.append((job_id, url, fresh, engine, variant, direct))
        start = not _QUEUE_RUNNING
        if start:
            _QUEUE_RUNNING = True
    if start:
        threading.Thread(target=_queue_worker, daemon=True).start()
    return job_id


class JobControlIn(BaseModel):
    job_id: str
    action: str            # pause | resume | stop


@app.post("/api/jobs/control")
def job_control(body: JobControlIn):
    """Pause, resume or stop a running ingest or render.

    Cooperative: the worker acts on it the next time it reports progress —
    between pipeline steps for an ingest, between clips for a render. A stop
    therefore lands within seconds rather than instantly, which is the honest
    trade for not killing the process.
    """
    action = (body.action or "").lower()
    if action not in ("pause", "resume", "stop"):
        raise HTTPException(400, "action must be pause, resume or stop")
    ctl = {"pause": "pause", "resume": "run", "stop": "stop"}[action]
    hit = False
    if body.job_id not in INGEST and body.job_id not in JOBS:
        rec0 = _load_ingest(body.job_id)       # a waiting job from before a restart
        if rec0 and rec0.get("status") in ("budget_paused", "interrupted", "held"):
            INGEST[body.job_id] = dict(rec0)
    for store, persist in ((INGEST, _persist_ingest), (JOBS, _persist_job)):
        rec = store.get(body.job_id)
        if rec is None:
            continue
        hit = True
        if rec.get("status") in ("done", "error", "cancelled"):
            raise HTTPException(409, f"job already {rec['status']} — nothing to "
                                     f"{action}")
        if rec.get("status") in ("budget_paused", "interrupted", "held"):
            # nothing is running for these: stop retires them, resume re-queues
            if ctl != "stop":
                raise HTTPException(409, "this job is waiting — use ▶ Resume to restart it")
            rec.update(status="cancelled", error="stopped by you", control="stop")
            persist(body.job_id)
            _after_ingest(body.job_id)
            continue
        rec["control"] = ctl
        if ctl == "stop":
            # a job still waiting in the queue never starts at all
            with _QUEUE_LOCK:
                before = len(_QUEUE)
                _QUEUE[:] = [q for q in _QUEUE if q[0] != body.job_id]
            if rec.get("status") == "queued" or len(_QUEUE) != before:
                rec.update(status="cancelled", error="stopped before it started")
                if store is INGEST:
                    persist(body.job_id)
                    _after_ingest(body.job_id)
        elif ctl == "pause" and rec.get("status") == "running":
            rec["status"] = "pausing"
        try:
            persist(body.job_id)
        except Exception:
            pass
    if not hit:
        raise HTTPException(404, "unknown job (it may predate this restart)")
    return {"ok": True, "job": body.job_id, "action": action}


class JobDelIn(BaseModel):
    job_ids: list[str] = []
    job_id: str = ""


@app.post("/api/jobs/delete")
def jobs_delete(body: JobDelIn):
    """Remove finished job records. A running job must be stopped first."""
    ids = [i for i in (list(body.job_ids) + ([body.job_id] if body.job_id else [])) if i]
    if not ids:
        raise HTTPException(400, "no job id given")
    deleted, skipped = [], []
    for jid in ids:
        rec = INGEST.get(jid) or JOBS.get(jid)
        if rec and rec.get("status") in ("running", "queued", "paused", "pausing"):
            skipped.append({"id": jid, "reason": f"still {rec['status']} — stop it first"})
            continue
        INGEST.pop(jid, None)
        JOBS.pop(jid, None)
        for name in (f"{jid}.json", f"render_{jid}.json"):
            try:
                os.remove(os.path.join(_JOBS_DIR, name))
            except OSError:
                pass
        deleted.append(jid)
    return {"ok": True, "deleted": deleted, "skipped": skipped}


class IngestIn(BaseModel):
    url: str
    fresh: bool = False    # S4: clear derived artifacts, regenerate all stages
    queue: bool = False    # run after any in-flight ingest instead of alongside
    # GEMINI IS THE DEFAULT and stays the default. Claude is the newer path;
    # a new path earns default status with data, it does not get it by being
    # newer. The choice is recorded per project so the two can be compared
    # like-for-like later.
    engine: str = "gemini"
    # Save as a VERSION beside the original ("v2" -> <chapter>-v2), leaving the
    # original project untouched. Empty = the chapter's own project, as before.
    variant: str = ""
    # Direct speech for this ingest: True/False, or None = the DIRECT_SPEECH
    # env default (off). docs/craft_reconciliation.md P2.
    direct_speech: bool | None = None
    # Narrator voice for this chapter ("gemini:Puck", "chirp:Charon"); empty =
    # the studio default. Applies when the chapter is first voiced or on a
    # fresh re-ingest — a voiced chapter keeps its voice.
    voice: str = ""
    voice_style: str | None = None


@app.post("/api/ingest")
def start_ingest(body: IngestIn):
    url = body.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "please paste a full chapter URL (http/https)")
    # DEDUPE: if this chapter is already ingesting, return that job — do not
    # start a second one (avoids a folder race + doubled Gemini/TTS spend).
    variant = (body.variant or "").strip().lower()
    import ingest as _ing_v
    if variant and not _ing_v.VARIANT_RE.match(variant):
        raise HTTPException(400, "version must be 1-16 lowercase letters, "
                                 "digits or dashes (e.g. v2)")
    if body.engine == "claude" and (variant or body.direct_speech is not None):
        raise HTTPException(400, "saving a version / choosing direct speech is "
                                 "Gemini-only for now")
    existing = _active_ingest_for_url(url, variant)
    if existing:
        return {"job": existing, "stages": ingest_stages(), "existing": True}
    # Validate the engine BEFORE the queue branch: a queued ingest used to skip
    # this check entirely (and then drop the engine), so it could neither be
    # refused up front nor actually run on Claude.
    import ingest as _ing
    if body.engine not in _ing.ENGINES:
        raise HTTPException(400, f"engine must be one of {_ing.ENGINES}")
    if body.voice:
        import gemini_tts as _gt
        try:
            _gt.save_choice(os.path.join(_ing.PROJECTS, _ing.project_id(url, variant)),
                            body.voice, body.voice_style)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if body.engine == "claude" and not _validator.api_key():
        raise HTTPException(400, "no Claude API key on this server — "
                                 "set CLAUDE_API_KEY or ANTHROPIC_API_KEY")
    if body.queue:
        job_id = _enqueue_ingest(url, body.fresh, body.engine, variant,
                                 body.direct_speech)
        return {"job": job_id, "stages": ingest_stages(), "existing": False,
                "fresh": body.fresh, "queued": True, "engine": body.engine}
    job_id = uuid.uuid4().hex[:12]
    INGEST[job_id] = {"stage": "queued", "pct": 0, "msg": "queued",
                      "status": "queued", "error": None, "project": None,
                      "url": url, "ts": time.time(), "control": "run",
                      "engine": body.engine, "variant": variant,
                      "direct_speech": body.direct_speech}
    _persist_ingest(job_id)
    threading.Thread(target=_run_ingest_job,
                     args=(job_id, url, body.fresh, body.engine, variant,
                           body.direct_speech),
                     daemon=True).start()
    return {"job": job_id, "stages": ingest_stages(), "existing": False,
            "fresh": body.fresh, "engine": body.engine}


def ingest_stages():
    import ingest
    return ingest.STAGES


@app.get("/api/ingest/status/{job_id}")
def ingest_status(job_id: str):
    j = _load_ingest(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get("/api/logs/usage")
def logs_usage(n: int = 100):
    """Tail the structured API-call log + today's running cost totals."""
    lines = []
    if os.path.exists(usage.LOG_PATH):
        with open(usage.LOG_PATH, encoding="utf-8") as f:
            raw = f.readlines()[-n:]
        for ln in raw:
            try:
                lines.append(json.loads(ln))
            except Exception:
                pass
    lines.reverse()  # newest first
    return {"calls": lines, "summary": usage.daily_summary()}


# ------------------------------------------------------------ Work log
# The owner must SEE the work without asking (2026-09-30). worklog.py reads
# memory.md (deployed with the code, so never stale) and serves evidence
# from the persistent volume — never from git (the repo is public).
@app.get("/api/worklog")
def api_worklog(limit: int = 60):
    import worklog as _wl
    es = _wl.entries(max(1, min(limit, 200)))
    for e in es:
        e["evidence_files"] = {s: _wl.evidence_files(s) for s in e["evidence"]}
    return {"deployed_commit": _wl.deployed_commit(), "entries": es}


@app.get("/api/evidence/{slug}/{name}")
def api_evidence_get(slug: str, name: str):
    import worklog as _wl
    p = _wl.evidence_path(slug, name)
    if not p or not os.path.isfile(p):
        raise HTTPException(404, "no such evidence file")
    return FileResponse(p, media_type=_wl.media_type(name))


@app.post("/api/evidence/{slug}/{name}")
async def api_evidence_put(slug: str, name: str, request: Request):
    """Store one evidence file (raw request body). Allowlisted names and
    types, 20 MB cap, overwrite-in-place — an agent or the owner can attach
    proof to a Work log entry through the API, like any other capability."""
    import worklog as _wl
    p = _wl.evidence_path(slug, name)
    if not p:
        raise HTTPException(400, "evidence names: letters, digits, . _ - ; "
                                 "png/jpg/webp/mp3/wav/txt/md/json only")
    body = await request.body()
    if not body or len(body) > _wl.MAX_BYTES:
        raise HTTPException(400, f"file must be 1 byte to {_wl.MAX_BYTES // 2**20} MB")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".part"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, p)
    return {"ok": True, "slug": slug, "name": name, "bytes": len(body)}


@app.get("/api/logs/ingest")
def logs_ingest():
    """Every ingest job (durable across restarts), newest first."""
    return {"jobs": _all_ingest_jobs()}


@app.get("/api/projects")
def projects_list():
    import ingest
    # The OPEN chapter is the one in active_project.txt. This list used to
    # put a legacy "chapter-2 (current)" placeholder first, always marked
    # active, and every real chapter as inactive — so Projects could never
    # show which chapter was actually open. The placeholder now appears only
    # when no chapter has been opened (the board then shows that workspace).
    active_id = get_active_project_id()
    projects = ingest.list_projects()
    known = {m.get("id") for m in projects}
    items = []
    if not active_id or active_id not in known:
        items.append({"id": "chapter-2 (current)", "url": "loaded", "active": True,
                      "n_segments": len(load_segments())})
    finished_slugs = set()
    for m in projects:
        items.append({**m, "active": m.get("id") == active_id})
        finished_slugs.add(m.get("id"))
    # IN-PROGRESS ingests (running/queued) — clickable to watch live status.
    in_progress = []
    for jid, j in INGEST.items():
        if j.get("status") in ("queued", "running"):
            in_progress.append({
                "job": jid, "url": j.get("url"),
                "slug": ingest._slug(j.get("url", "")),
                "stage": j.get("stage"), "pct": j.get("pct", 0),
                "status": j.get("status"), "source": j.get("source", "manual"),
            })
    _add_review_status(items)
    return {"projects": items, "in_progress": in_progress}


def _add_review_status(items):
    """The Projects inbox: where each chapter is in review → approve →
    render → publish → archive, read from files the studio already writes
    (no new state). Also marks autopilot-made chapters."""
    import ingest
    import autopilot as _ap
    import project_archive as _arch
    try:
        auto = {e.get("project") for e in _ap.load(ingest.PROJECTS)["ledger"].values()
                if e.get("source") == "autopilot"}
    except Exception:
        auto = set()
    rendering = {j.get("project") for j in JOBS.values()
                 if j.get("type") == "finalize" and j.get("status") in ("queued", "running", "paused", "pausing")}
    try:
        one = {r["id"]: r["status"] for r in _chapter_rows()}     # the single status (rebuild step 2)
    except Exception:
        one = {}
    for it in items:
        pid = it.get("id")
        if pid in one:
            it["status"] = one[pid]
        pdir = os.path.join(ingest.PROJECTS, pid or "")
        if not pid or not os.path.isdir(pdir):
            continue
        it["auto"] = pid in auto
        arch = _arch.view(pdir)
        it["archive"] = arch
        try:
            approved = bool(json.load(open(os.path.join(pdir, "storyboard.json"))).get("approved"))
        except (OSError, ValueError):
            approved = False
        try:
            n_exports = len([f for f in os.listdir(os.path.join(pdir, "exports"))
                             if f.endswith(".mp4")])
        except OSError:
            n_exports = 0
        try:
            rep = json.load(open(os.path.join(pdir, "validation.json")))
            it["checks"] = len(rep.get("findings") or [])
        except (OSError, ValueError):
            it["checks"] = None
        published = _arch.is_published(load_publishes(pdir))
        if arch:
            st = "archived"
        elif published:
            st = "published"
        elif pid in rendering:
            st = "rendering"
        elif n_exports:
            st = "rendered"
        elif approved:
            st = "approved"
        else:
            st = "ready"
        it["review_status"] = st
        # approved, nothing rendering, no video on disk (deleted by hand or by
        # retention) — say so instead of a plain "Approved"
        it["video_missing"] = st == "approved" and not n_exports


class ActivateIn(BaseModel):
    id: str


@app.get("/api/tracker")
def api_tracker(refresh: int = 0):
    """New-chapter tracker: what we have vs what the source has published.

    Cached for tracker.TTL_SECONDS; pass refresh=1 to re-check now. A series
    whose check FAILED reports an error rather than looking up to date — the
    scraper bug taught us that silence reads as success.
    """
    import ingest as _ing
    import tracker as _trk
    return _trk.build(_ing.list_projects(), _ing.PROJECTS, refresh=bool(refresh))


class UntrackIn(BaseModel):
    series_url: str
    restore: bool = False


@app.post("/api/tracker/untrack")
def api_tracker_untrack(body: UntrackIn):
    """Remove a series from the watchlist, or put it back.

    Deliberately NOT destructive: the ingested chapters, clips and exports are
    untouched. Deleting actual project data is the Projects tab's job, which
    asks for confirmation; this only stops the tracker checking for new
    chapters, and is reversible from the same panel.
    """
    import ingest as _ing
    import tracker as _trk
    url = (body.series_url or "").strip()
    if not url:
        raise HTTPException(400, "which series?")
    if body.restore:
        _trk.retrack(_ing.PROJECTS, url)
    else:
        _trk.untrack(_ing.PROJECTS, url)
    return _trk.build(_ing.list_projects(), _ing.PROJECTS)


# ===================================================================
# WATCHLIST — canonical series, mirrors, and chapter-level ingest.
#
# The tracker answers "what is new in what I already own". These routes
# answer the question that comes BEFORE money is spent: what should we make
# next, where can it be read, and which chapter do we pull. The two coexist;
# nothing below changes /api/tracker.
# ===================================================================
def _wl_root():
    import ingest as _ing
    return _ing.PROJECTS


def _wl_view():
    import ingest as _ing
    import watchlist as _wl
    return _wl.view(_wl_root(), _ing.list_projects())


class WLSeriesIn(BaseModel):
    title: str
    url: str = ""
    tier: str = "watchlist"
    rank: int | None = None
    aliases: list[str] | None = None
    keywords: list[str] | None = None
    notes: str = ""


class WLMirrorIn(BaseModel):
    series_id: str
    url: str


class WLPrefIn(BaseModel):
    series_id: str
    series_key: str


class WLUpdateIn(BaseModel):
    series_id: str
    title: str | None = None
    tier: str | None = None
    rank: int | None = None
    notes: str | None = None
    aliases: list[str] | None = None
    keywords: list[str] | None = None


class WLIngestIn(BaseModel):
    series_id: str
    series_key: str
    chapter: str
    fresh: bool = False
    queue: bool = True
    engine: str = "gemini"   # same choice, default and checks as /api/ingest


@app.get("/api/watchlist")
def api_watchlist():
    return _wl_view()


@app.post("/api/watchlist/seed")
def api_watchlist_seed():
    """Load the owner's demand research. Idempotent — an existing series keeps
    whatever has since been edited about it."""
    import watchlist as _wl
    res = _wl.seed(_wl_root())
    return {"seeded": res, "watchlist": _wl_view()}


@app.post("/api/watchlist/series")
def api_watchlist_add(body: WLSeriesIn):
    """Add a title BEFORE anything is ingested — the whole point of a
    watchlist. A source URL is optional; a title with no mirror yet is a
    legitimate state (we want it, we have not found where to read it)."""
    import watchlist as _wl
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "a title is required")
    s = _wl.add_series(_wl_root(), title, aliases=body.aliases,
                       tier=body.tier, rank=body.rank,
                       keywords=body.keywords, notes=body.notes)
    url = (body.url or "").strip()
    if url:
        try:
            _wl.add_mirror(_wl_root(), s["id"], url)
        except _wl.WatchlistError as e:
            raise HTTPException(409, str(e))
    return {"series_id": s["id"], "watchlist": _wl_view()}


class WLQuickIn(BaseModel):
    url: str
    tier: str = "greenlight"
    make_chapter: bool = False


def _quick_followup(series_id, series_key):
    """After a paste: check the source (chapters, dates, cover), then research
    the cast if the series has none. Background; reported in Logs → Live."""
    import watchlist as _wl
    try:
        m = _wl.refresh_mirror(_wl_root(), series_id, series_key)
        _ev("tracker", f"{series_id}: source checked — " + (
            f"{m.get('chapter_count')} chapters, latest ch.{m.get('latest')}" if m.get("status") == "ok"
            else f"could not read it: {m.get('error')}"), "ok" if m.get("status") == "ok" else "warn")
    except Exception as e:  # noqa
        _ev("tracker", f"{series_id}: source check failed: {e}", "error")
    try:
        s = _research._series(series_id)
        if s and _research.needs_research(s):
            b = _research.build(series_id)
            _ev("research", f"{b.get('canonical_title', series_id)}: "
                            f"{(_research.STATUS.get(series_id) or {}).get('summary', 'done')}", "ok")
    except usage.UsageCapExceeded as e:
        _ev("research", f"{series_id}: research waits — spend cap reached ({e})", "warn")
    except Exception as e:  # noqa
        _ev("research", f"{series_id}: research failed: {str(e)[:160]}", "error")


@app.post("/api/watchlist/quick")
def api_watchlist_quick(body: WLQuickIn):
    """Paste a link, done (owner, 2026-10-04): a series OR chapter link from a
    supported site. The title comes from the page, the series is created (or the
    existing one reused — never a duplicate), its source is attached, and in the
    background its chapters are checked and its cast researched. A chapter link
    can also queue that chapter."""
    import ingest as _i
    import providers
    import watchlist as _wl
    url = (body.url or "").strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "paste a full link (https://…)")
    d = providers.describe(url)
    if d["support"] != "supported":
        raise HTTPException(400, f"{d['label']} links can't be tracked yet — use an Asura or WEBTOON link")
    data = _wl.load(_wl_root())
    s, _m = _wl.find_by_mirror(data, url)
    created = False
    if s is None:
        title = None
        try:
            title = providers.series_title(providers.fetch(d["series_url"]))
        except Exception:
            title = None
        if not title:
            series, _c = _i.parse_series_chapter(url)
            title = _i.to_title_case(_i.clean_series_slug(series or "")) or "Untitled series"
        s = _wl.find(data, _wl.slugify(title))
        if s is None:
            s = _wl.add_series(_wl_root(), title, tier=body.tier if body.tier in _wl.TIERS else "greenlight")
            created = True
        try:
            _wl.add_mirror(_wl_root(), s["id"], url)
        except _wl.WatchlistError as e:
            raise HTTPException(409, str(e))
        _ev("tracker", f"added {title} ({d['label']}) from a pasted link")
    s = _wl.find(_wl.load(_wl_root()), s["id"])
    canon = providers.canonical_key(d["series_key"])
    key = next((m["series_key"] for m in s["mirrors"] if providers.canonical_key(m["series_key"]) == canon),
               (s["mirrors"] or [{}])[0].get("series_key"))
    threading.Thread(target=_quick_followup, args=(s["id"], key), daemon=True).start()
    chapter = providers.chapter_of(url)
    job = None
    note = None
    if chapter and body.make_chapter:
        pid = _i.project_id(url)
        busy = any(j.get("status") in ("queued", "running") and j.get("url") == url for j in INGEST.values())
        if busy:
            note = f"ch.{chapter} is already being made"
        elif os.path.exists(os.path.join(_i.PROJECTS, pid, "segments.json")):
            note = f"ch.{chapter} is already made — open it in Projects"
        else:
            job = _enqueue_ingest(url, source="manual", why="from a pasted link")
    return {"ok": True, "series_id": s["id"], "title": s["title"], "created": created,
            "source": d["label"], "chapter": chapter, "job": job, "note": note}


@app.post("/api/watchlist/mirror")
def api_watchlist_mirror(body: WLMirrorIn):
    """Attach another place the same story can be read."""
    import watchlist as _wl
    url = (body.url or "").strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "paste a full series URL (http/https)")
    try:
        m = _wl.add_mirror(_wl_root(), body.series_id, url)
    except _wl.WatchlistError as e:
        raise HTTPException(409, str(e))
    return {"mirror": m, "watchlist": _wl_view()}


@app.post("/api/watchlist/preferred")
def api_watchlist_preferred(body: WLPrefIn):
    import watchlist as _wl
    try:
        _wl.set_preferred(_wl_root(), body.series_id, body.series_key)
    except _wl.WatchlistError as e:
        raise HTTPException(400, str(e))
    return _wl_view()


@app.post("/api/watchlist/update")
def api_watchlist_update(body: WLUpdateIn):
    import watchlist as _wl
    try:
        _wl.update_series(_wl_root(), body.series_id, title=body.title,
                          tier=body.tier, rank=body.rank, notes=body.notes,
                          aliases=body.aliases, keywords=body.keywords)
    except _wl.WatchlistError as e:
        raise HTTPException(404, str(e))
    return _wl_view()


@app.post("/api/watchlist/remove")
def api_watchlist_remove(body: WLPrefIn):
    """Drop a title from the watchlist. Never touches ingested project data —
    same rule as untrack."""
    import watchlist as _wl
    _wl.remove_series(_wl_root(), body.series_id)
    return _wl_view()


@app.get("/api/watchlist/chapters")
def api_watchlist_chapters(series_id: str, series_key: str, refresh: int = 0):
    """What this mirror has, with what we already hold marked.

    A refresh that FAILS reports the error instead of an empty list — the
    tracker's lesson: silence must never render as 'no chapters'.
    """
    import watchlist as _wl
    data = _wl.load(_wl_root())
    s = _wl.find(data, series_id)
    if s is None:
        raise HTTPException(404, "not on the watchlist")
    m = next((x for x in s["mirrors"] if x["series_key"] == series_key), None)
    if m is None:
        raise HTTPException(404, "that mirror is not attached to this series")
    if refresh or m.get("status") == "unchecked":
        m = _wl.refresh_mirror(_wl_root(), series_id, series_key)
    have = set()
    view = next((v for v in _wl_view()["series"] if v["id"] == series_id), {})
    for vm in view.get("mirrors", []):
        for c in vm.get("ingested", []):
            have.add(str(c["chapter"]))
    return {"source": m["source"], "label": m["label"], "status": m["status"],
            "error": m["error"], "last_checked": m["last_checked"],
            "support": m["support"],
            "chapters": [{"id": c, "ingested": str(c) in have}
                         for c in m.get("chapters", [])]}


@app.post("/api/watchlist/refresh_all")
def api_watchlist_refresh_all():
    """Check every series' preferred (else best) mirror: chapters, release
    dates and cover. Sequential and bounded — one page per series."""
    import watchlist as _wl
    data = _wl.load(_wl_root())
    done, failed = 0, []
    for srs in data["series"][:60]:
        key = srs.get("preferred_mirror") or (_wl.best_mirror(srs) or {}).get("series_key")
        if not key:
            continue
        m = _wl.refresh_mirror(_wl_root(), srs["id"], key)
        if m.get("status") == "ok":
            done += 1
        else:
            failed.append({"series": srs["title"], "error": m.get("error")})
    return {"checked": done, "failed": failed}


@app.get("/api/watchlist/cover/{series_id}")
def api_watchlist_cover(series_id: str):
    """A series' cover, fetched once from its own site (with that site as
    referer — WEBTOON's image CDN refuses hotlinks) and cached on the volume."""
    import re as _re
    import urllib.parse as _up
    import urllib.request as _ur
    import watchlist as _wl
    import providers as _pv
    if not _re.fullmatch(r"[a-z0-9-]{1,120}", series_id):
        raise HTTPException(404, "not found")
    d = os.path.join(_wl_root(), "_covers")
    os.makedirs(d, exist_ok=True)
    for ext, mt in (("jpg", "image/jpeg"), ("png", "image/png"), ("webp", "image/webp")):
        p = os.path.join(d, f"{series_id}.{ext}")
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < 7 * 86400:
            return FileResponse(p, media_type=mt, headers={"Cache-Control": "public, max-age=86400"})
    srs = _wl.find(_wl.load(_wl_root()), series_id)
    if srs is None:
        raise HTTPException(404, "not on the watchlist")
    m = next((x for x in srs.get("mirrors", []) if x.get("cover")), None)
    if m is None:
        raise HTTPException(404, "no cover yet — check the series first")
    cover, page = m["cover"], m["series_url"]
    ch, ph = _up.urlparse(cover).hostname or "", _up.urlparse(page).hostname or ""
    same_site = ".".join(ch.split(".")[-2:]) == ".".join(ph.split(".")[-2:])
    if _up.urlparse(cover).scheme != "https" or not (same_site or ch.endswith(".pstatic.net")):
        raise HTTPException(404, "cover is not on the series' own site")
    req = _ur.Request(cover, headers={"User-Agent": _pv.UA, "Referer": page})
    try:
        with _ur.urlopen(req, timeout=20, context=_pv._ctx()) as r:
            data, mt = r.read(8_000_000), (r.headers.get_content_type() or "image/jpeg")
    except Exception:
        raise HTTPException(502, "could not fetch the cover")
    ext = {"image/png": "png", "image/webp": "webp"}.get(mt, "jpg")
    with open(os.path.join(d, f"{series_id}.{ext}"), "wb") as f:
        f.write(data)
    return Response(data, media_type=mt, headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/watchlist/ingest")
def api_watchlist_ingest(body: WLIngestIn):
    """Ingest one chapter from one mirror of one canonical series.

    This is the join between planning and the existing pipeline: the provider
    builds the chapter URL, then the ORIGINAL /api/ingest path runs unchanged,
    so cost guardrails, dedupe and queueing all still apply.
    """
    import watchlist as _wl
    data = _wl.load(_wl_root())
    s = _wl.find(data, body.series_id)
    if s is None:
        raise HTTPException(404, "not on the watchlist")
    try:
        url = _wl.chapter_url(s, body.series_key, body.chapter)
    except _wl.WatchlistError as e:
        raise HTTPException(400, str(e))
    if not url:
        raise HTTPException(400, "this source cannot build a chapter URL yet")
    res = start_ingest(IngestIn(url=url, fresh=body.fresh, queue=body.queue,
                                engine=body.engine))
    return dict(res, url=url, title=s["title"], chapter=body.chapter)


def _render_lock(except_pid=None):
    """The chapter a render is working on right now, if any other than
    except_pid. Renders read the ACTIVE chapter's timeline and audio clip by
    clip, so switching the active chapter mid-render would mix two chapters
    into one video (found 2026-10-05, before the owner rendered 12 chapters)."""
    for x in list(JOBS.values()):
        if ((x.get("type") == "finalize" or "seg_indices" in x)
                and x.get("status") in ("running", "queued", "paused", "pausing")
                and x.get("project") and x.get("project") != except_pid):
            return x["project"]
    return None


@app.post("/api/activate")
def activate_project(body: ActivateIn):
    """Point the studio at an ingested project: load its segments + audio."""
    global AUDIO_DIR, DESCRIPTIONS
    import ingest
    busy = _render_lock(body.id)
    if busy:
        raise HTTPException(409, f"{_pretty(busy)} is rendering. Only one chapter can be open for "
                                 f"rendering or editing at a time — try again when it finishes "
                                 f"(usually a few minutes).")
    proj = os.path.join(ingest.PROJECTS, body.id)
    seg = os.path.join(proj, "segments.json")
    if not os.path.exists(seg):
        raise HTTPException(404, "project not found")

    # Persist the active project ID
    try:
        os.makedirs(WORK, exist_ok=True)
        with open(os.path.join(WORK, "active_project.txt"), "w", encoding="utf-8") as f:
            f.write(body.id)
    except Exception as e:
        print(f"Failed to save active project file: {e}", file=sys.stderr)

    AUDIO_DIR = os.path.join(proj, "audio")
    pdesc = os.path.join(proj, "descriptions.json")
    if os.path.exists(pdesc):
        DESCRIPTIONS = pdesc

    # Initialize review.json if not present
    rpath = os.path.join(proj, "review.json")
    if not os.path.exists(rpath):
        try:
            with open(rpath, "w", encoding="utf-8") as f:
                json.dump({}, f)
        except Exception as e:
            print(f"Failed to init review.json: {e}", file=sys.stderr)

    # Load segments count
    try:
        with open(seg, encoding="utf-8") as f:
            segs = json.load(f)
    except Exception:
        segs = []

    return {"ok": True, "id": body.id, "n_segments": len(segs)}


@app.get("/api/media")
def media():
    """The active chapter's panel library — every real (non-junk) panel with a
    thumbnail — for the Media tab. Sourced from the active project's
    descriptions (kept in sync by /api/activate)."""
    import matcher
    panels = [p for p in _load_descriptions()
              if p.get("ok", True) and not matcher.is_junk_panel(p)]
    # which panels are currently placed on the timeline?
    used = {s["panel_id"] for s in load_segments()}
    out = [{
        "panel_id": p["panel_id"],
        "thumb_url": f"/panelimg/{p['panel_id']}?thumb=1",
        "ocr": (p.get("ocr_text") or "")[:80],
        "desc": (p.get("visual_description") or "")[:120],
        "used": p["panel_id"] in used,
    } for p in panels]
    return {"panels": out, "count": len(out), "used": len(used)}


# ---- D1: interactive storyboard (pre-render review gate) ----------------
from fastapi.responses import HTMLResponse
import storyboard as _storyboard


def _approval_path():
    return os.path.join(active_project_dir(), "storyboard.json")


def storyboard_approved():
    try:
        return bool(json.load(open(_approval_path())).get("approved"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


@app.get("/storyboard")
def storyboard_page():
    """The combined review table (approved template): EVERY extracted panel,
    OCR/description, script placement, render timing, live controls."""
    import matcher
    return HTMLResponse(_storyboard.build_storyboard_html(
        active_project_dir(), matcher, load_review(),
        usage.daily_summary(), storyboard_approved()))


# ---- Claude validation chain (pre-owner review gate) --------------------
import validator as _validator
from datetime import datetime, timezone


def _run_validate_job(job_id, pdir, mode):
    """The validator can take a minute on a long chapter, so it runs in the
    same background-job machinery renders use — the board polls /api/jobs/{id}
    and the run survives the tab being closed. It is also registered in the
    Logs drawer, so a validation is never silent work."""
    j = JOBS[job_id]
    j["status"] = "running"
    _persist_job(job_id)

    def progress(msg):
        j["stage"] = msg
        j["done"] = min(j.get("done", 0) + 1, j["total"])
        _persist_job(job_id)

    try:
        rep = _validator.validate(pdir, review=load_review(), mode=mode,
                                  progress=progress)
        # A report whose own status is 'error' (a refused batch, a missing key,
        # a cap hit) must NOT show as a green job — that is exactly how a
        # half-run validation gets mistaken for a clean board.
        j["status"] = "done" if rep.get("status") == "ok" else "error"
        j["error"] = rep.get("error")
        j["stage"] = ("%d finding(s) · $%.4f" %
                      (len(rep.get("findings", [])), rep.get("cost_usd", 0.0)))
        j["report"] = {k: rep.get(k) for k in
                       ("status", "counts", "cost_usd", "calls", "rows",
                        "model", "mode", "elapsed_sec")}
        j["done"] = j["total"]
    except Exception as e:                       # never leave a job "running"
        j["status"] = "error"
        j["error"] = str(e)[:500]
    _persist_job(job_id)


@app.post("/api/validate")
async def start_validation(request: Request):
    """Run the review chain over the active project's board.

    mode: 'rules' (free, no credentials), 'text' (rules + Claude text), or
    'full' (adds the Claude vision pass on flagged rows only).
    """
    body = await request.json() if await request.body() else {}
    mode = (body or {}).get("mode", "full")
    if mode not in ("rules", "text", "full"):
        raise HTTPException(400, "mode must be rules, text or full")

    pdir = active_project_dir()
    job_id = uuid.uuid4().hex[:12]
    # 'total' is a progress denominator, not a promise: batches + a capped
    # vision pass + the rule pass. The bar is approximate and the stage text
    # carries the truth.
    n_rows = len(_validator.build_rows(pdir))
    steps = 1 + (0 if mode == "rules"
                 else -(-n_rows // _validator.BATCH_ROWS))
    if mode == "full":
        steps += 4
    JOBS[job_id] = {"status": "queued", "done": 0, "total": max(1, steps),
                    "kind": "validate", "mode": mode, "error": None,
                    "stage": "queued"}
    threading.Thread(target=_run_validate_job,
                     args=(job_id, pdir, mode), daemon=True).start()
    return {"job": job_id, "mode": mode, "rows": n_rows}


class ValidateActionIn(BaseModel):
    action: str
    params: dict = {}


@app.post("/api/validate/action")
def validate_action(body: ValidateActionIn):
    """Apply one Check finding action to the REAL project.

    Wrapped in the same undo snapshot the board's own editor ops use, so a fix
    applied from Check lands in one undo step and is indistinguishable from the
    same fix made by hand on the board.
    """
    import validator_actions
    pdir = active_project_dir()
    if body.action in validator_actions.MUTATING:
        _snapshot()
    try:
        out = validator_actions.apply_action(pdir, body.action, body.params)
    except validator_actions.ActionError as e:
        raise HTTPException(400, str(e))

    # A board change invalidates the findings that described the old board.
    # Stamp the stored report so the drawer can say "these findings predate
    # your last fix" instead of presenting them as current.
    if out.get("report_stale"):
        rep = _validator.load_report(pdir)
        if rep:
            rep["stale"] = True
            rep["stale_since"] = datetime.now(timezone.utc).isoformat()
            _validator.save_report(pdir, rep)
    return out


@app.get("/api/validation")
def get_validation():
    """The last saved report for the active project, or a null report so the
    board can say 'never validated' rather than showing nothing."""
    rep = _validator.load_report(active_project_dir())
    if rep is None:
        return {"report": None,
                "model": _validator.MODEL, "effort": _validator.EFFORT}
    return {"report": rep, "model": _validator.MODEL,
            "effort": _validator.EFFORT}


# ---- EXPERIMENT: the non-audio pipeline driven by Claude ----------------
# Isolated on purpose. These routes never write the production
# descriptions.json / script.json / segments.json — everything the experiment
# produces lives under <project>/claude_test/, and the board never reads it.
import claude_pipeline as _ctest


def _run_claude_test_job(job_id, pdir, stages, model):
    j = JOBS[job_id]
    j["status"] = "running"
    _persist_job(job_id)

    def progress(msg):
        j["stage"] = msg
        j["done"] = min(j.get("done", 0) + 1, j["total"])
        _persist_job(job_id)

    try:
        man = _ctest.run(pdir, stages=stages, model=model, progress=progress)
        j["status"] = "done" if man.get("status") == "ok" else "error"
        j["error"] = man.get("error")
        j["stage"] = ("%d panels · %d calls · $%.4f" %
                      (man.get("panels", 0), man.get("calls", 0),
                       man.get("cost_usd", 0.0)))
        j["done"] = j["total"]
    except Exception as e:
        j["status"] = "error"
        j["error"] = str(e)[:500]
    _persist_job(job_id)


# ---- TEST LAB: an independent Claude-driven chapter pipeline ------------
import claude_lab as _lab


def _run_lab_job(job_id, url, splitter, fresh):
    j = JOBS[job_id]
    j["status"] = "running"
    _persist_job(job_id)

    def progress(msg):
        j["stage"] = msg
        j["done"] = min(j.get("done", 0) + 1, j["total"])
        _persist_job(job_id)

    try:
        man = _lab.run_lab(url, splitter=splitter, progress=progress,
                           job_id=job_id, fresh=fresh)
        j["status"] = "done" if man.get("status") == "ok" else "error"
        j["error"] = man.get("error")
        j["project"] = man.get("project")
        j["stage"] = (("%s — %d panels · %d lines · %d segments · $%.4f" %
                       (man.get("project", ""), man.get("panels", 0),
                        man.get("units", 0), man.get("segments", 0),
                        man.get("cost_usd", 0.0)))
                      if man.get("status") == "ok"
                      else ("failed: " + str(man.get("error"))[:160]))
        j["done"] = j["total"]
    except Exception as e:
        j["status"] = "error"
        j["error"] = str(e)[:500]
    _persist_job(job_id)


class LabRunIn(BaseModel):
    url: str
    splitter: str = "claude"
    fresh: bool = False


class SplitRunIn(BaseModel):
    url: str = ""
    project: str = ""
    blur: bool = False


def _splitlab_pages(body):
    """Where the page images come from: an already-ingested project, or a
    chapter URL scraped on demand. Scraping is free, so a preview never needs
    the chapter to be ingested first."""
    import ingest as _ing
    import splitlab as _sl
    if body.project:
        pdir = os.path.join(_ing.PROJECTS, os.path.basename(body.project))
        pages = os.path.join(pdir, "pages")
        if not os.path.isdir(pages):
            raise HTTPException(404, f"no pages for project {body.project}")
        slug = os.path.basename(body.project)
    else:
        url = (body.url or "").strip()
        if not re.match(r"^https?://", url):
            raise HTTPException(400, "paste a full chapter URL, or pick a project")
        slug = _ing._slug(url)
        pages = os.path.join(_sl.runs_dir(slug), "_pages")
        if not os.path.isdir(pages) or not os.listdir(pages):
            os.makedirs(pages, exist_ok=True)
            sys.path.insert(0, RECAP)
            import scraper
            scraper.download_chapter(url, pages)
    files = sorted(os.path.join(pages, f) for f in os.listdir(pages)
                   if os.path.splitext(f)[1].lower()
                   in (".png", ".jpg", ".jpeg", ".webp"))
    if not files:
        raise HTTPException(400, "no page images found")
    return slug, files


@app.post("/api/split/run")
def split_run(body: SplitRunIn):
    """Preview the background-registration splitter. No model calls, no spend."""
    import splitlab as _sl
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "done": 0, "total": 1, "kind": "split",
                    "stage": "queued", "error": None, "ts": time.time(),
                    "heartbeat": time.time(),
                    "project": body.project or body.url}

    def work():
        j = JOBS[job_id]
        j["status"] = "running"
        _persist_job(job_id)
        try:
            slug, files = _splitlab_pages(body)
            j["slug"] = slug
            j["total"] = len(files)

            def prog(m):
                # Stop is checked between pages, which is the only point a
                # split can be interrupted safely: a half-written crop set
                # would look like a finished run with pages missing.
                _control_gate(JOBS, job_id, _persist_job)
                j["stage"] = m
                j["done"] = min(j["done"] + 1, j["total"])
                _persist_job(job_id)

            meta = _sl.run(slug, files, on_progress=prog, blur=body.blur)
            j["status"] = "done"
            j["stage"] = (f"{meta['panels']} panels from {meta['pages']} "
                          f"{meta['format']} image(s)")
            j["slug"] = slug
            j["done"] = j["total"]
        except JobCancelled:
            j["status"] = "cancelled"
            j["stage"] = "stopped — partial results discarded"
            import shutil as _sh
            _sh.rmtree(_sl.runs_dir(j.get("slug") or ""), ignore_errors=True)
        except HTTPException as e:
            j["status"] = "error"
            j["error"] = str(e.detail)[:300]
        except Exception as e:
            j["status"] = "error"
            j["error"] = str(e)[:300]
        _persist_job(job_id)

    threading.Thread(target=work, daemon=True).start()
    return {"job": job_id}


class DescribePanelsIn(BaseModel):
    slug: str
    limit: int = 0          # 0 = every panel in the run
    # Long-edge cap for the vision call. 1568 is the API's effective maximum:
    # beyond it the service downscales server-side, so larger values buy
    # tokens rather than resolution. Overridden per call so a legibility
    # experiment changes ONE variable without restarting the service.
    max_px: int = 0         # 0 = leave the module default alone
    tile: bool = False      # split over-tall panels into overlapping tiles


@app.post("/api/describe/panels")
def describe_panels(body: DescribePanelsIn):
    """OCR a stored panel set server-side, where the Claude key already lives.

    Built so a benchmark can be OCR'd for cents and RE-OCR'd whenever it
    changes, instead of a one-off full-chapter run that answers the question
    once. The OCR gate consumes `ocr` + `ocr_confidence`; Split Lab panels have
    neither, because splitting makes no model calls.

    Returns COUNTS ONLY. The dialogue text itself is never sent back — the
    caller needs to know whether a panel has text and how confident the reader
    was, not what the characters said.
    """
    import claude_plus as CP
    import splitlab as _sl
    import usage as _u

    slug = os.path.basename(body.slug or "")
    run = _sl.runs_dir(slug)
    if not os.path.isdir(run):
        raise HTTPException(404, f"no split run '{slug}'")
    if not _validator.api_key():
        raise HTTPException(400, "no Claude API key on this server")

    names = sorted(f for f in os.listdir(run)
                   if f.endswith(".png") and not f.endswith("_blur.png"))
    if body.limit:
        names = names[:body.limit]
    if not names:
        raise HTTPException(400, "that run has no panels")

    from PIL import Image
    panels = []
    for i, f in enumerate(names, start=1):
        try:
            w, h = Image.open(os.path.join(run, f)).size
        except OSError:
            continue
        panels.append({"panel_id": os.path.splitext(f)[0], "file": f,
                       "width": w, "height": h, "n": i, "_n": i})

    # describe_plus resolves images as <pdir>/crops/<file>; the run stores them
    # flat. A symlinked view avoids copying a few hundred MB to satisfy a path.
    work = tempfile.mkdtemp(prefix="ocrbench_")
    link = os.path.join(work, "crops")
    try:
        os.symlink(run, link)
    except OSError:
        os.makedirs(link, exist_ok=True)
        for f in names:
            shutil.copyfile(os.path.join(run, f), os.path.join(link, f))

    job_id = "ocrbench_" + slug[:24]
    _u.set_job(job_id)

    import claude_pipeline as CPL
    prev = CPL.VISION_MAX_PX
    if body.max_px:
        if body.max_px > 1568:
            raise HTTPException(400, "max_px above 1568 buys tokens, not "
                                     "resolution — the API downscales past it")
        CPL.VISION_MAX_PX = int(body.max_px)
    try:
        descs, st = CP.describe_plus(work, panels)
    finally:
        CPL.VISION_MAX_PX = prev

    out = os.path.join(run, "descriptions.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(descs, f, indent=2)

    # KEY NAME MATTERS: describe_plus stores the transcription as `ocr_text`,
    # not `ocr`. Counting the wrong key reported with_ocr=0 for all 57 panels
    # and sent a legibility investigation (and real money) chasing a failure
    # that did not exist. Both spellings are accepted so neither producer can
    # silently read as empty again.
    def _ocr_of(d):
        return str(d.get("ocr_text") or d.get("ocr") or "").strip()

    with_ocr = sum(1 for d in descs if _ocr_of(d))
    conf = [d.get("ocr_confidence") for d in descs
            if d.get("ocr_confidence") is not None]
    # Legibility is a function of how much the panel was shrunk, so the
    # response carries the height bins rather than a single average that
    # would hide the cliff.
    by_h = {"<1200": [0, 0], "1200-1568": [0, 0],
            "1569-2500": [0, 0], ">2500": [0, 0]}
    hmap = {p["panel_id"]: p["height"] for p in panels}
    for d in descs:
        h = hmap.get(d.get("panel_id"), 0)
        k = ("<1200" if h < 1200 else "1200-1568" if h <= 1568
             else "1569-2500" if h <= 2500 else ">2500")
        by_h[k][1] += 1
        if _ocr_of(d):
            by_h[k][0] += 1
    return {"slug": slug, "panels": len(descs),
            "max_px": body.max_px or prev,
            "fill_by_height": {k: {"with_ocr": v[0], "panels": v[1]}
                               for k, v in by_h.items()},
            "empty_ids": [d.get("panel_id") for d in descs
                          if not _ocr_of(d)],
            "with_ocr": with_ocr, "empty_ocr": len(descs) - with_ocr,
            "carrying_confidence": len(conf),
            "mean_confidence": round(sum(conf) / len(conf), 3) if conf else None,
            "calls": st.get("calls"), "cost_usd": st.get("cost_usd")}


@app.get("/api/describe/panels/{slug}")
def describe_panels_read(slug: str):
    """The gate-relevant fields for each panel — deliberately NOT the text."""
    import splitlab as _sl
    run = _sl.runs_dir(os.path.basename(slug))
    try:
        with open(os.path.join(run, "descriptions.json"), encoding="utf-8") as f:
            descs = json.load(f)
    except (OSError, ValueError):
        raise HTTPException(404, "that run has not been OCR'd")
    return {"slug": slug, "panels": [
        {"panel_id": d.get("panel_id"),
         "has_ocr": bool(str(d.get("ocr_text") or d.get("ocr") or "").strip()),
         "ocr_len": len(str(d.get("ocr_text") or d.get("ocr") or "").strip()),
         "height": d.get("height"),
         "ocr_confidence": d.get("ocr_confidence"),
         "desc_confidence": d.get("desc_confidence"),
         "subject_type": d.get("subject_type"),
         "needs_review": d.get("needs_review")}
        for d in descs]}


@app.get("/api/split/runs")
def split_runs():
    import splitlab as _sl
    return {"runs": [{k: v for k, v in m.items() if k != "panel_list"}
                     for m in _sl.list_runs()]}


@app.get("/api/split/run/{slug}")
def split_run_detail(slug: str):
    import splitlab as _sl
    d = _sl.runs_dir(os.path.basename(slug))
    try:
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        raise HTTPException(404, "no such split run")


@app.get("/splitimg/{slug}/{name}")
def split_image(slug: str, name: str):
    import splitlab as _sl
    p = os.path.join(_sl.runs_dir(os.path.basename(slug)), os.path.basename(name))
    if not os.path.exists(p):
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="image/png")


@app.post("/api/lab/run")
async def lab_run(body: LabRunIn):
    """Build a whole chapter independently, from a URL, with Claude making the
    decisions. Produces a REAL project — not a report — so the board, Check,
    approve, export and Review all work on the result with no special cases."""
    url = (body.url or "").strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "paste a full http(s) chapter URL")
    if body.splitter not in _lab.SPLITTERS:
        raise HTTPException(400, f"splitter must be one of {_lab.SPLITTERS}")
    if not _validator.api_key():
        raise HTTPException(
            400, "No Claude API key on this server — set CLAUDE_API_KEY or "
                 "ANTHROPIC_API_KEY in the environment.")
    job_id = uuid.uuid4().hex[:12]
    # ts IS NOT OPTIONAL. /api/jobs sorts by it and truncates to `limit`, so a
    # record without one sorts as 0 and falls off the end of the list — which
    # is exactly why finished lab runs were invisible in the Logs drawer while
    # their money had already been spent.
    JOBS[job_id] = {"status": "queued", "done": 0, "total": 60,
                    "kind": "lab", "error": None, "stage": "queued",
                    "url": url, "splitter": body.splitter,
                    "ts": time.time(),
                    # Known up front, not only on completion — the card needs
                    # to match a LIVE run to its row while it is still running.
                    "project": _lab.lab_id(url, body.splitter)}
    threading.Thread(target=_run_lab_job,
                     args=(job_id, url, body.splitter, body.fresh),
                     daemon=True).start()
    return {"job": job_id, "project": _lab.lab_id(url, body.splitter),
            "splitter": body.splitter}


@app.get("/api/lab/projects")
def lab_projects():
    """Every lab chapter built so far, with what it cost and whether it is
    ready to open on the board."""
    out = []
    try:
        names = sorted(os.listdir(_lab.PROJECTS))
    except FileNotFoundError:
        names = []
    active = os.path.basename(active_project_dir().rstrip("/"))
    for n in names:
        if "-lab-" not in n:
            continue
        d = os.path.join(_lab.PROJECTS, n)
        man = _lab.manifest(d) or {}
        meta = _project_label(d)
        segs = 0
        try:
            with open(os.path.join(d, "segments.json"), encoding="utf-8") as f:
                segs = len(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        out.append({
            "project": n, "title": meta["title"] or n,
            "series": meta["series"], "chapter": meta["chapter"],
            "splitter": man.get("splitter"), "status": man.get("status"),
            "error": man.get("error"), "url": man.get("url"),
            "panels": man.get("panels"), "units": man.get("units"),
            "segments": segs, "duration": man.get("duration"),
            "cost_usd": man.get("cost_usd"), "calls": man.get("calls"),
            "elapsed_sec": man.get("elapsed_sec"),
            # READY MEANS RENDERABLE. It used to mean "segments.json exists",
            # so a chapter whose audio did not fit its windows still showed a
            # green light and then failed at render. A build that predates the
            # check has timeline=None and keeps the old meaning rather than
            # being wrongly marked broken.
            "timeline": man.get("timeline"),
            "timeline_errors": (man.get("timeline") or {}).get("errors"),
            "ready": segs > 0 and ((man.get("timeline") or {}).get("errors")
                                   in (None, 0)),
            "active": n == active,
        })
    return {"projects": out, "splitters": list(_lab.SPLITTERS),
            "key_configured": bool(_validator.api_key())}


@app.get("/api/lab/report")
def lab_report(project: str | None = None):
    """Operational diagnostics for one lab run — what the pipeline measured
    about its OWN work, which is what says where to go next."""
    import claude_compare
    pdir = (os.path.join(_lab.PROJECTS, os.path.basename(project))
            if project else active_project_dir())
    if not os.path.isdir(pdir):
        raise HTTPException(404, "no such project")
    try:
        return claude_compare.lab_report(pdir)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/test/status")
def claude_test_status():
    """What the TEST tab needs to decide what it can offer: does this project
    have a Gemini baseline, has the experiment run, what did it cost."""
    pdir = active_project_dir()
    meta = _project_label(pdir)
    baseline = _ctest.baseline_panels(pdir)
    man = _ctest.load_manifest(pdir)
    return {
        "project": os.path.basename(pdir.rstrip("/")),
        "title": meta["title"], "series": meta["series"],
        "chapter": meta["chapter"],
        "baseline_panels": len(baseline),
        "has_baseline": bool(baseline),
        "manifest": man,
        "has_claude": bool(_ctest.read(pdir, "descriptions.json")),
        "model": _ctest.MODEL,
        "key_configured": bool(_validator.api_key()),
        "stages": list(_ctest.STAGES),
        "panels_per_call": _ctest.PANELS_PER_CALL,
    }


class ClaudeTestIn(BaseModel):
    stages: list[str] = list(_ctest.STAGES)
    model: str | None = None


@app.post("/api/test/run")
def claude_test_run(body: ClaudeTestIn):
    pdir = active_project_dir()
    bad = [s for s in body.stages if s not in _ctest.STAGES]
    if bad:
        raise HTTPException(400, f"unknown stage(s): {', '.join(bad)}")
    n = len(_ctest.baseline_panels(pdir))
    if not n:
        raise HTTPException(
            400, "This project has not been ingested, so there are no panel "
                 "crops to run the experiment on and no baseline to compare "
                 "against. Ingest the chapter normally first.")
    job_id = uuid.uuid4().hex[:12]
    # Progress denominator: one tick per vision batch plus a few for the
    # script/place/timing stages. Approximate by design — the stage text
    # carries the truth.
    steps = -(-n // _ctest.PANELS_PER_CALL) + 3
    JOBS[job_id] = {"status": "queued", "done": 0, "total": max(1, steps),
                    "kind": "claude-test", "error": None, "stage": "queued"}
    threading.Thread(target=_run_claude_test_job,
                     args=(job_id, pdir, tuple(body.stages), body.model),
                     daemon=True).start()
    return {"job": job_id, "panels": n, "stages": body.stages}


@app.get("/api/test/rows")
def claude_test_rows():
    """The Claude variant as board rows, so the tab can show the five columns
    exactly as the real board does."""
    import claude_compare
    pdir = active_project_dir()
    try:
        return {"rows": claude_compare._claude_rows(pdir)}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/test/compare")
def claude_test_compare():
    import claude_compare
    pdir = active_project_dir()
    try:
        return claude_compare.compare(pdir, review=load_review())
    except ValueError as e:
        raise HTTPException(400, str(e))


def _run_promote_job(job_id, pdir):
    j = JOBS[job_id]
    j["status"] = "running"
    _persist_job(job_id)

    def progress(msg):
        j["stage"] = msg
        j["done"] = min(j.get("done", 0) + 1, j["total"])
        _persist_job(job_id)

    try:
        out = _ctest.promote(pdir, progress=progress)
        j["status"] = "done"
        j["stage"] = ("built %s — %d segments, %.0fs" %
                      (out["project"], out["segments"], out["duration"]))
        j["result"] = out
        j["done"] = j["total"]
    except Exception as e:
        j["status"] = "error"
        j["error"] = str(e)[:500]
    _persist_job(job_id)


@app.post("/api/test/promote")
def claude_test_promote():
    """Turn the Claude experiment into a REAL, playable project.

    Runs Claude's script through the STANDARD TTS path and builds render
    segments from Claude's own placement and crops — so the result is a normal
    project the board, approve, export and Review pages already understand, and
    can actually be watched. The baseline is never written to; the result is a
    sibling project you can delete.
    """
    pdir = active_project_dir()
    if not _ctest.read(pdir, "script.json"):
        raise HTTPException(
            400, "Run the Claude pipeline first — there is no experiment "
                 "output to promote yet.")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "done": 0, "total": 12,
                    "kind": "claude-promote", "error": None, "stage": "queued"}
    threading.Thread(target=_run_promote_job, args=(job_id, pdir),
                     daemon=True).start()
    return {"job": job_id, "project": _ctest.promoted_id(pdir)}


@app.post("/api/test/reset")
def claude_test_reset():
    """Throw the experiment away. It is a sidecar, so this cannot touch the
    production board."""
    import shutil
    d = _ctest.out_dir(active_project_dir())
    if os.path.isdir(d):
        shutil.rmtree(d)
    return {"ok": True, "removed": os.path.basename(d)}


@app.get("/")
def root_redirect():
    """Storyboard is the main page (v2); the legacy UI lives at /legacy/."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/storyboard")


@app.get("/legacy/")
def legacy_ui():
    """Archived original review UI (cold storage, still functional)."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "static", "legacy", "index.html")
    if not os.path.exists(p):
        raise HTTPException(404, "legacy UI not present in this build")
    return HTMLResponse(open(p, encoding="utf-8").read())


@app.get("/api/storyboard/approval")
def storyboard_approval():
    return {"approved": storyboard_approved()}


# ---- storyboard editor v2 ops (P2-P6): every op rewrites segments.json ----
def _sb_op(fn, *a, **kw):
    import storyboard_edit
    _snapshot()                       # undo covers editor ops too
    try:
        segs = fn(active_project_dir(), *a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    total = round(segs[-1]["start"] + segs[-1]["dur"], 3) if segs else 0
    return {"ok": True, "n_segments": len(segs), "total": total}


class IncludeIn(BaseModel):
    panel_id: str
    hold: float = 2.5     # silent-hold seconds for script-less panels


@app.post("/api/storyboard/include")
def sb_include(body: IncludeIn):
    import storyboard_edit
    pdir = active_project_dir()
    scenes = []
    try:
        scenes = json.load(open(os.path.join(pdir, "script.json")))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    descs = json.load(open(os.path.join(pdir, "descriptions.json")))
    return _sb_op(storyboard_edit.include_panel, body.panel_id,
                  scenes, descs, hold=body.hold)


class SetIncludedIn(BaseModel):
    panel_id: str | None = None
    all: bool = False
    included: bool


@app.post("/api/storyboard/set_included")
def sb_set_included(body: SetIncludedIn):
    """T3: the user's final-video inclusion flag. Never set by the system —
    segments are born unchecked; only checked ones export/concat/render."""
    segs = load_segments()
    hit = 0
    for s in segs:
        if body.all or s.get("panel_id") == body.panel_id:
            s["user_included"] = body.included
            hit += 1
    if not hit:
        raise HTTPException(404, "no segment for that panel")
    _write_segments(segs)
    return {"ok": True, "updated": hit,
            "included": sum(1 for s in segs if s.get("user_included"))}


class ExcludeIn(BaseModel):
    panel_id: str


@app.post("/api/storyboard/exclude")
def sb_exclude(body: ExcludeIn):
    import storyboard_edit
    return _sb_op(storyboard_edit.exclude_panel, body.panel_id)


class SegIn(BaseModel):
    seg_index: int


@app.post("/api/storyboard/delete")
def sb_delete(body: SegIn):
    """Hard-delete one image slot. Undo covers it (snapshot first)."""
    import storyboard_edit
    _snapshot()
    try:
        segs, info = storyboard_edit.delete_segment(active_project_dir(),
                                                    body.seg_index)
    except (ValueError, IndexError, StopIteration) as e:
        raise HTTPException(400, str(e) or "segment not found")
    total = round(segs[-1]["start"] + segs[-1]["dur"], 3) if segs else 0
    return {"ok": True, "n_segments": len(segs), "total": total, **info}


@app.post("/api/storyboard/duplicate")
def sb_duplicate(body: SegIn):
    """Copy an image into a new silent slot right after it."""
    import storyboard_edit
    _snapshot()
    try:
        segs, info = storyboard_edit.duplicate_segment(active_project_dir(),
                                                       body.seg_index)
    except (ValueError, IndexError, StopIteration) as e:
        raise HTTPException(400, str(e) or "segment not found")
    total = round(segs[-1]["start"] + segs[-1]["dur"], 3) if segs else 0
    return {"ok": True, "n_segments": len(segs), "total": total, **info}


class DurationIn(BaseModel):
    seg_index: int
    dur: float


@app.post("/api/storyboard/duration")
def sb_duration(body: DurationIn):
    import storyboard_edit
    return _sb_op(storyboard_edit.set_duration, body.seg_index, body.dur)


class BoundaryIn(BaseModel):
    seg_index: int
    delta: float          # + grows this segment into the next; - shrinks


@app.post("/api/storyboard/boundary")
def sb_boundary(body: BoundaryIn):
    import storyboard_edit
    return _sb_op(storyboard_edit.move_boundary, body.seg_index, body.delta)


class SegOnlyIn(BaseModel):
    seg_index: int


@app.post("/api/storyboard/use_full_panel")
def sb_use_full_panel(body: SegOnlyIn):
    """P3: reviewer overrides a bad planner crop with the whole panel.

    Writes crop_bbox_norm=[0,0,1,1] into segments.json — the SAME manifest the
    exporter reads — so the override reaches the video, not just the board.
    The planner's box is preserved in crop_bbox_norm_ai and is restorable.
    """
    import storyboard_edit
    out = _sb_op(storyboard_edit.use_full_panel, body.seg_index)
    out["crop"] = "full"
    return out


@app.post("/api/storyboard/restore_crop")
def sb_restore_crop(body: SegOnlyIn):
    import storyboard_edit
    out = _sb_op(storyboard_edit.restore_ai_crop, body.seg_index)
    out["crop"] = "planner"
    return out


class RepairIn(BaseModel):
    dry_run: bool = False


@app.post("/api/storyboard/repair_slices")
def sb_repair_slices(body: RepairIn):
    """Repair the timeline faults that block rendering.

    Runs all three, because the pre-render error used to point operators here
    for a fault this endpoint could not actually fix: a beat scheduled outside
    its own segment (left behind when a deleted segment handed its audio to a
    later sibling) is a single record, so the slice repairs skipped it entirely
    and the render stayed blocked with no way forward.
    """
    import storyboard_edit
    if not body.dry_run:
        _snapshot()
    try:
        pdir = active_project_dir()
        # One shared sequence (storyboard_edit.repair_all) — the lab and the
        # Gemini ingest run the same one, so the three can never drift apart.
        return storyboard_edit.repair_all(pdir, dry_run=body.dry_run)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/validate")
def api_validate():
    """P0: the timing/crop contract for the active project. Read-only."""
    import storyboard_edit
    try:
        return storyboard_edit.validate_timeline(active_project_dir())
    except storyboard_edit.TimelineNotReady as e:
        # 200 with ready=False, not a 500: the board polls this and a project
        # mid-ingest is a normal state.
        return {"ok": True, "ready": False, "reason": str(e),
                "errors": [], "warnings": [], "n_errors": 0,
                "n_warnings": 0, "n_segments": 0}


def _gate_timeline(seg_indexes, action):
    """Refuse to render/export a timeline that would cut narration off."""
    import storyboard_edit
    try:
        v = storyboard_edit.validate_timeline(active_project_dir())
    except storyboard_edit.TimelineNotReady as e:
        raise HTTPException(409, f"{action} blocked — {e}")
    want = set(seg_indexes)
    errs = [e for e in v["errors"] if e["seg"] in want]
    if errs:
        detail = "; ".join(f"seg {e['seg']}: {e['msg']}" for e in errs[:6])
        raise HTTPException(400,
            f"{action} blocked — {len(errs)} timing error(s) would cut narration. "
            f"{detail}"
            + (f" (+{len(errs) - 6} more)" if len(errs) > 6 else "")
            + ". Run POST /api/storyboard/repair_slices — it re-binds swapped "
              "slices, collapses overlapping duplicates of one sentence, AND "
              "re-seats beats scheduled outside their own segment.")


class MoveIn(BaseModel):
    seg_index: int
    to: int               # target playback position (0-based)


@app.post("/api/storyboard/move")
def sb_move(body: MoveIn):
    import storyboard_edit
    return _sb_op(storyboard_edit.reorder, body.seg_index, body.to)


class AssignIn(BaseModel):
    seg_index: int
    panel_id: str
    move_here: bool = False   # also move to that panel's reading-order spot


@app.post("/api/storyboard/assign")
def sb_assign(body: AssignIn):
    """Free placement: drop a seg card onto ANY row. The narration/timing
    stay put; only the artwork changes (move_here also re-sequences)."""
    import storyboard_edit
    return _sb_op(storyboard_edit.assign_panel, body.seg_index, body.panel_id,
                  _load_descriptions(), body.move_here)


class AddLineIn(BaseModel):
    seg_index: int
    text: str


@app.post("/api/storyboard/addline")
def sb_addline(body: AddLineIn):
    import storyboard_edit
    return _sb_op(storyboard_edit.add_line, body.seg_index, body.text,
                  _synth_rest)      # usage-gated TTS, hash cache applies


class ApproveIn(BaseModel):
    approved: bool
    rerender_all: bool = False   # force: rebuild every ticked clip
    keep_voice: bool = False     # render in the chapter's own voice, no re-voice
    force_start: bool = False    # internal: queue worker starting the single allowed render


class DelLineIn(BaseModel):
    seg_index: int
    beat_index: int


@app.post("/api/storyboard/delline")
def sb_delline(body: DelLineIn):
    """M8: drop one narration line from the video (text + audio)."""
    import storyboard_edit
    _snapshot()
    try:
        storyboard_edit.delete_line(active_project_dir(), body.seg_index,
                                    body.beat_index)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/storyboard/undo")
def storyboard_undo():
    """Walk the timeline back one edit.

    Restores the manifest as it stood before the last mutating op, rather than
    applying an inverse — several ops here slice audio or synthesise it, and
    "apply the opposite" would be fifteen separate chances to drift.
    """
    import storyboard_edit as edit
    pdir = active_project_dir()
    try:
        res = edit.undo(pdir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return res


@app.get("/api/storyboard/undo")
def storyboard_undo_stack():
    """What undo would walk back through — so the button can name the edit."""
    import storyboard_edit as edit
    return {"stack": edit.undo_stack(active_project_dir())}


@app.post("/api/storyboard/approve")
def storyboard_approve(body: ApproveIn):
    import time
    json.dump({"approved": body.approved, "ts": time.time()},
              open(_approval_path(), "w"))
    if not body.approved:
        return {"ok": True, "approved": False}
    # USER CONTRACT: approving RENDERS the ticked segments and EXPORTS the
    # final narrated video, as one visible background job.
    segs = load_segments()
    ticked = video_segments(segs)
    if getattr(body, "rerender_all", False):
        pdir = active_project_dir()
        for s_ in ticked:                      # drop clips so all rebuild
            cp = os.path.join(pdir, s_.get("clip", ""))
            if s_.get("clip") and os.path.exists(cp):
                os.remove(cp)
        try:
            os.remove(_epochs_path())
        except OSError:
            pass
    if not ticked:
        return {"ok": True, "approved": True, "job": None,
                "note": "nothing ticked — tick segments, then approve again"}

    curr_pid = get_active_project_id()
    # Concurrency rule: NEVER RENDER MULTIPLE CHAPTERS AT THE SAME TIME.
    # If another chapter is currently rendering and this is not the queue worker starting it, queue it!
    if not getattr(body, "force_start", False):
        active_other = _render_lock(except_pid=curr_pid)
        is_running = any(j.get("type") == "finalize" and j.get("status") in ("queued", "running") for j in JOBS.values())
        if active_other or is_running:
            it, why = _rq.add(_ingest_mod.PROJECTS, curr_pid, keep_voice=bool(body.keep_voice))
            _rq_kick()
            pos = _rq.position(_ingest_mod.PROJECTS, curr_pid) or {}
            return {"ok": True, "approved": True, "job": None, "queued": True, "render_queue": pos,
                    "note": f"Another chapter is rendering — queued #{pos.get('place', 1)} in line (never renders simultaneously)"}

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"type": "finalize", "status": "queued", "stage": "queued",
                    "done": 0, "total": 0, "current_seg": None, "error": None,
                    "export": None, "url": None, "ts": time.time(),
                    "project": curr_pid, "keep_voice": bool(body.keep_voice)}
    _persist_job(job_id)
    threading.Thread(target=_run_finalize_job, args=(job_id,),
                     daemon=True).start()
    return {"ok": True, "approved": True, "job": job_id,
            "n_render": len([s for s in ticked if not os.path.exists(
                os.path.join(active_project_dir(), s.get("clip", "")))]),
            "n_ticked": len(ticked)}


# ---- E7: project backup (volume data is as precious as unpushed code) ---
@app.get("/api/backup/{project_id}")
def backup_project(project_id: str):
    """Tar.gz of one project's PAID artifacts: descriptions (Gemini spend),
    audio (TTS spend), scripts, segments, review state. Excludes clips/ and
    crops/ — both regenerable locally at zero API cost."""
    import io
    import tarfile
    import ingest as _ing
    pdir = os.path.join(_ing.PROJECTS, project_id)
    if not os.path.isdir(pdir) or "/" in project_id or ".." in project_id:
        raise HTTPException(404, "unknown project")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(pdir):
            dirs[:] = [d for d in dirs if d not in ("clips", "crops")]
            for fn in files:
                full = os.path.join(root, fn)
                tar.add(full, arcname=os.path.relpath(full, _ing.PROJECTS))
    buf.seek(0)
    from fastapi.responses import Response
    return Response(content=buf.read(), media_type="application/gzip",
                    headers={"Content-Disposition":
                             f'attachment; filename="{project_id}.tar.gz"'})


@app.get("/health")
def liveness():
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    # Verify workspace is writable
    test_file = os.path.join(WORK, ".ready_test")
    try:
        with open(test_file, "w") as f:
            f.write("ready")
        os.remove(test_file)
        return {"status": "ready"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Workspace not writeable: {e}")


@app.get("/api/health")
def health():
    import usage
    import shutil
    
    # Disk space check on WORK directory
    total_gb, used_gb, free_gb, pct = 0.0, 0.0, 0.0, 0.0
    try:
        total, used, free = shutil.disk_usage(WORK)
        total_gb = round(total / (1024**3), 2)
        used_gb = round(used / (1024**3), 2)
        free_gb = round(free / (1024**3), 2)
        pct = round((used / total) * 100, 1)
    except Exception:
        pass
        
    # Usage caps check
    summary = usage.daily_summary()
    gemini_calls = summary.get("gemini_calls", 0)
    tts_chars = summary.get("tts_chars", 0)
    est_cost_usd = summary.get("est_cost_usd", 0.0)
    
    warnings = []
    if pct > 85.0:
        warnings.append(f"Low disk space: {pct}% used on persistent volume.")
    if gemini_calls >= usage.MAX_DAILY_GEMINI_CALLS * 0.9:
        warnings.append(f"Gemini daily calls are near limit ({gemini_calls}/{usage.MAX_DAILY_GEMINI_CALLS}).")
    if tts_chars >= usage.MAX_DAILY_TTS_CHARS * 0.9:
        warnings.append(f"TTS daily characters are near limit ({tts_chars}/{usage.MAX_DAILY_TTS_CHARS}).")
    if est_cost_usd >= usage.daily_cap() * 0.9:
        warnings.append(f"Daily spend is near cap (${est_cost_usd:.2f}/${usage.daily_cap():.2f}).")
        
    status = "OK"
    if len(warnings) > 0:
        status = "WARNING"
    if pct > 95.0 or est_cost_usd >= usage.daily_cap():
        status = "CRITICAL"
        
    return {
        "status": status,
        "disk": {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": pct
        },
        "caps": {
            "gemini_calls": gemini_calls,
            "gemini_limit": usage.MAX_DAILY_GEMINI_CALLS,
            "tts_chars": tts_chars,
            "tts_limit": usage.MAX_DAILY_TTS_CHARS,
            "est_cost_usd": est_cost_usd,
            "spend_limit_usd": usage.daily_cap(),
            "warnings": warnings
        },
        "config": {
            "gemini_api_key_configured": bool(os.environ.get("GEMINI_API_KEY")),
            "tts_api_key_configured": bool(os.environ.get("TTS_API_KEY") or os.environ.get("GEMINI_API_KEY"))
        }
    }


# Debug endpoints (/api/debug/test-planner, /ps, /cat) removed 2026-10-04 in the rebuild cleanup:
# they were left over from Session 21 debugging and no page used them.


# ================================================================ AUTOPILOT
# Chapter Autopilot (owner request 2026-10-03): ingest new chapters of every
# watchlist series on its own, one at a time, round robin, under the daily
# cap; they land in Projects as "Ready for review". Plus resume after stops,
# restarts and cap pauses, and archive-after-publish. Logic in autopilot.py
# and project_archive.py; this is the wiring. OFF by default.
import autopilot as _autopilot
import project_archive as _archive

_SCHED = {"started": False, "last_run": None, "last_error": None}


def _ap_deps():
    import providers as _prov
    import watchlist as _wl

    def refresh():
        for s in _wl_view()["series"]:
            if s.get("best_mirror"):
                try:
                    _wl.refresh_mirror(_wl_root(), s["id"], s["best_mirror"])
                except Exception as e:  # noqa
                    print(f"[autopilot] refresh {s['id']} failed: {e}", flush=True)

    def live_projects():
        return {_ingest_mod.project_id(j.get("url", ""), j.get("variant", ""))
                for j in list(INGEST.values())
                if j.get("status") in ("queued", "running", "paused", "pausing")}

    def running_projects():
        return {_job_pid(j) for j in list(INGEST.values()) if j.get("status") == "running"}

    def queue_busy():
        # A chapter paused by a usage limit today also holds the line: starting
        # another would only pause too (owner, 2026-10-06: ch.182 started and
        # paused right after ch.181). They resume first when there is room.
        return any(j.get("status") in ("queued", "running", "paused", "pausing")
                   for j in list(INGEST.values())) or bool(_paused_today()) or \
            any(j.get("status") == "held" for j in list(INGEST.values()))

    def spend():
        d = usage.daily_summary()
        spent = d.get("est_cost_usd", 0.0) if d.get("date") == usage._today() else 0.0
        return float(spent or 0.0), float(usage.daily_cap())

    def chapter_url(row, chapter):
        m = row["mirror"]
        return _prov.by_name(m["source"]).chapter_url(m["series_url"], chapter)

    return {"view": lambda: _wl_view()["series"], "refresh": refresh, "ap_spend": _ap_spent_today,
            "canon": _prov.canonical_key, "live_projects": live_projects,
            "queue_busy": queue_busy, "running_projects": running_projects, "spend": spend, "chapter_url": chapter_url,
            "project_id": lambda url: _ingest_mod.project_id(url),
            "enqueue": lambda url, engine: _enqueue_ingest(url, False, engine, "", None,
                                                           source="autopilot")}


_AP_SPEND_CACHE = {"at": 0.0, "value": 0.0}


def _ap_spent_today():
    """Metered spend of autopilot's own jobs today (ET), from the usage log —
    the number autopilot's $ budget is checked against. Cached 30 s."""
    now = time.time()
    if now - _AP_SPEND_CACHE["at"] < 30:
        return _AP_SPEND_CACHE["value"]
    jobs = {e.get("job") for e in _autopilot.load(_ingest_mod.PROJECTS)["ledger"].values()
            if e.get("source") == "autopilot" and e.get("job")}
    today, total = _autopilot.et_day(now), 0.0
    if jobs:
        try:
            with open(usage.LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    if '"job_id"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("job_id") in jobs and _autopilot.et_day(
                            datetime.fromisoformat(e["ts"]).timestamp()) == today:
                        total += e.get("est_cost_usd") or 0.0
        except (FileNotFoundError, KeyError, ValueError):
            pass
    _AP_SPEND_CACHE.update(at=now, value=round(total, 4))
    return _AP_SPEND_CACHE["value"]


def _waiting_ingests(status):
    return [j for j in _all_ingest_jobs() if j.get("status") == status]


# ---- the line (owner, 2026-10-06: "avoid more than 2 chapters at once — 3
# paused/resumed and 1 running gets messy and doesn't account for daily
# limits"). At most LINE_MAX chapters are in the line (queued, running or
# paused). Others wait OUTSIDE it as "held" and start by themselves, oldest
# first, when a place frees up; one paused by a daily limit only re-enters
# when there is room under the limits.
LINE_MAX = max(1, int(os.environ.get("INGEST_LINE_MAX", "2")))
_LINE_ST = ("queued", "running", "paused", "pausing")


def _line_count():
    return sum(1 for j in list(INGEST.values()) if j.get("status") in _LINE_ST)


def _hold(job_id, why="waiting its turn — at most %d chapters are made at once; it starts by itself" % LINE_MAX):
    rec = INGEST.get(job_id) or _load_ingest(job_id) or {}
    rec.update(status="held", stage="held", msg=why, error=None)
    INGEST[job_id] = rec
    _persist_ingest(job_id)
    with _QUEUE_LOCK:
        _QUEUE[:] = [q for q in _QUEUE if q[0] != job_id]


def _fill_line():
    """Start waiting chapters, oldest first, until the line is full."""
    n = 0
    waiting = sorted(_waiting_ingests("held") + _waiting_ingests("budget_paused"),
                     key=lambda x: x.get("ts", 0))
    today = usage._today()
    for j in waiting:
        if _line_count() >= LINE_MAX:
            break
        if j.get("status") == "budget_paused" and not _cap_headroom():
            continue
        why = ("resumed: its turn came" if j.get("status") == "held" else
               "resumed: a new spend day started" if j.get("paused_day") != today else
               "resumed: there is room under the limits again")
        _enqueue_ingest(j.get("url", ""), False, j.get("engine", "gemini"), j.get("variant", ""),
                        j.get("direct_speech"), job_id=j["job"], why=why)
        _autopilot.set_status_for_job(_ingest_mod.PROJECTS, j["job"], "queued")
        n += 1
    return n


def _trim_line():
    """If more than LINE_MAX are in the line (e.g. from before this rule),
    hold the newest ones that haven't started."""
    over = _line_count() - LINE_MAX
    if over <= 0:
        return 0
    waiting = sorted((dict(j, id=k) for k, j in list(INGEST.items()) if j.get("status") == "queued"),
                     key=lambda x: -(x.get("ts") or 0))
    for j in waiting[:over]:
        _hold(j["id"])
    return min(over, len(waiting))


def _resume_interrupted():
    """Startup: re-queue ingests a restart cut off (see the boot sweep) — at
    most LINE_MAX; the rest wait their turn ("held")."""
    n = 0
    for j in sorted(_waiting_ingests("interrupted"), key=lambda x: x.get("ts", 0)):
        jid = j["job"]
        rec = _load_ingest(jid) or {}
        rec["resumes"] = rec.get("resumes", 0) + 1
        INGEST[jid] = rec
        if _line_count() >= LINE_MAX:
            _hold(jid, "waiting its turn after a server restart; it starts by itself")
            continue
        ctl = "pause" if rec.get("control") == "pause" else "run"
        _enqueue_ingest(rec.get("url", ""), False, rec.get("engine", "gemini"),
                        rec.get("variant", ""), rec.get("direct_speech"), job_id=jid,
                        control=ctl, why=f"resumed after a server restart ({rec['resumes']} of {RESUME_MAX})")
        _autopilot.set_status_for_job(_ingest_mod.PROJECTS, jid, "queued")
        n += 1
    # chapters waiting their turn show in the jobs bar right after a restart
    # (the bar reads INGEST; they were only on disk until their turn came)
    for j in _waiting_ingests("held"):
        if j["job"] not in INGEST:
            rec = _load_ingest(j["job"])
            if rec:
                INGEST[j["job"]] = rec
    if n:
        print(f"[boot] resumed {n} interrupted ingest(s)", flush=True)
    return n


def _cap_headroom():
    """Room under EVERY daily limit for one more chapter right now (spend,
    AI calls, voice characters). Used to resume chapters the moment a limit
    is raised, not only at midnight (owner, 2026-10-06)."""
    try:
        d = usage.daily_summary()
        if d.get("date") != usage._today():
            return True
        est = _autopilot.estimate(_autopilot.load(_ingest_mod.PROJECTS)["ledger"])
        return (float(d.get("est_cost_usd") or 0) + est <= usage.daily_cap()
                and int(d.get("gemini_calls") or 0) + 700 <= usage.MAX_DAILY_GEMINI_CALLS
                and int(d.get("tts_chars") or 0) + 15000 <= usage.MAX_DAILY_TTS_CHARS)
    except Exception:
        return False


def _paused_today():
    today = usage._today()
    return [j for j in _waiting_ingests("budget_paused") if j.get("paused_day") == today]


def _resume_budget_paused():
    """Scheduler step: trim an over-full line, then fill it from chapters
    waiting their turn or paused by a limit (see _fill_line)."""
    _trim_line()
    return _fill_line()


def _resume_budget_paused_old():
    """(replaced by _fill_line; kept for reference)"""
    today = usage._today()
    room = _cap_headroom()
    n = 0
    for j in sorted(_waiting_ingests("budget_paused"), key=lambda x: x.get("ts", 0)):
        if j.get("paused_day") == today and not room:
            continue
        _enqueue_ingest(j.get("url", ""), False, j.get("engine", "gemini"), j.get("variant", ""),
                        j.get("direct_speech"), job_id=j["job"],
                        why=("resumed: a new spend day started" if j.get("paused_day") != today
                             else "resumed: there is room under the limits again"))
        _autopilot.set_status_for_job(_ingest_mod.PROJECTS, j["job"], "queued")
        n += 1
    return n


def _archive_sweep(now=None):
    """Archive published chapters; delete archived folders whose date passed."""
    import ingest
    out = {"archived": [], "deleted": []}
    active = get_active_project_id()
    for m in ingest.list_projects():
        pid = m.get("id")
        pdir = os.path.join(ingest.PROJECTS, pid or "")
        if not pid or not os.path.isdir(pdir):
            continue
        st = _archive.read(pdir)
        if _cs.board_newer(pdir):
            # being worked on again (re-run / new version after posting):
            # never archive or delete it; bring it back if it was archived
            if st is not None:
                _archive.unarchive(pdir)
                _ev("archive", f"{_pretty(pid)}: back in the library — its board was rebuilt after the posted video")
            continue
        if st is None and _archive.is_published(load_publishes(pdir)):
            _archive.archive(pdir, "published", now)
            _autopilot.audit("project_archived", project=pid, reason="published")
            out["archived"].append(pid)
        elif st is not None and _archive.due(pdir, now) and pid != active:
            ok, why, mb = _delete_one_project(pid, why="cleaned up after posting (archive)")
            _autopilot.audit("archived_project_deleted" if ok else "archived_delete_skipped",
                             project=pid, detail=why, freed_mb=mb)
            if ok:
                out["deleted"].append(pid)
    return out


def _targets_of(x):
    """The channels one queued video posts to (its own setting, else Settings')."""
    try:
        pdir = project_dir_for(x["project"])
    except HTTPException:
        return []
    return list(({**publish_defaults(pdir), **(load_publish(pdir).get(x["name"]) or {})}).get("targets") or [])


def _close_stale_failures():
    """A failed post whose chapter was re-rendered since is closed (owner,
    2026-10-07: 'I re-rendered — why is it in Errors AND Needs review?')."""
    import ingest as _i

    def newest(project):
        try:
            return _cs._latest_export(project_dir_for(project))
        except HTTPException:
            return None
    try:
        return _pq.drop_stale_failures(_i.PROJECTS, newest)
    except Exception:
        return 0


def _local_post_blockers(project, name):
    """The publish checks that need no network: the file exists, it is
    approved, the cut hasn't changed since, the metadata passes. (The full
    publish_eligibility also asks Upload-Post about the channel.)"""
    try:
        pdir = project_dir_for(project)
    except HTTPException:
        return ["the chapter folder is gone"]
    out = []
    if not _export_stat(pdir, name):
        return ["the video file no longer exists"]
    rv = review_state(pdir, name)
    if rv["status"] != "approved":
        out.append("not approved in Review")
    if rv["superseded"]:
        out.append("the board changed after you approved it — watch and approve the new render")
    md = {**publish_defaults(pdir), **(load_publish(pdir).get(name) or {})}
    out += validate_publish(md, pdir, name)
    return out


SCHEDULE_MAX_TRIES = 10


def _reconcile_posting():
    """Like Scrapper's reconcile_posting: every queued item that's "posting"
    asks Upload-Post whether YouTube finished, so a finished upload stops
    showing "uploading now…" (owner, 2026-10-07: Murim Psychopath ch.45 was
    live on YouTube but showed uploading for a day — nothing ever asked)."""
    import ingest as _i
    n = 0
    for it in list(_pq.load(_i.PROJECTS)["items"]):
        if it["status"] != "posting":
            continue
        try:
            os_publish_status(it["project"], it["name"])
            n += 1
        except Exception as e:  # noqa — one bad record never stops the rest
            print(f"[reconcile] {it['project']}: {str(e)[:160]}", flush=True)
    if n:
        _pq.sync(_i.PROJECTS, _pub_status)
    return n


def _schedule_post_pass(now=None):
    """Posting schedule (step 5): one post per due time slot, through the same
    checked publish job as Post now. Off unless switched on.

    Owner, 2026-10-07 ("11, 3 and 9 … none have gone out — look at Scrapper"):
    a video that can't post no longer costs the slot. It is marked with the
    reason (Posting schedule → Errors, and Needs you) and the SAME slot goes to
    the next queued video that can post, like Scrapper's queue, where one bad
    item never blocks the day."""
    import ingest as _i
    try:
        _reconcile_posting()
    except Exception:
        pass
    sched = _studio.load().get("schedule") or {}
    if not sched.get("enabled"):
        return None
    _pq.sync(_i.PROJECTS, _pub_status)
    refused = []
    dec = None
    for _try in range(SCHEDULE_MAX_TRIES):
        dec = _pq.decide(sched, _pq.load(_i.PROJECTS), now or time.time(), _targets_of)
        if dec["action"] == "skip":
            why = dec["why"] + (f" (could not post: {', '.join(refused)})" if refused else "")
            _pq.slot_done(_i.PROJECTS, dec["day"], dec["slot"], "skipped: " + why)
            _ev("publish", f"schedule {dec['slot']}: nothing posted — {why}", "warn")
            break
        if dec["action"] != "post":
            break
        it = dec["item"]
        blockers = _local_post_blockers(it["project"], it["name"])
        try:
            if blockers:
                raise HTTPException(409, "; ".join(blockers))
            r = studio_queue_post(StudioItemIn(id=it["id"]))
            _pq.mark(_i.PROJECTS, it["id"], posted_day=dec["day"])
            _pq.slot_done(_i.PROJECTS, dec["day"], dec["slot"], f"posted {it['project']} ({r.get('privacy')})"
                          + (f" after skipping {', '.join(refused)}" if refused else ""))
            _ev("publish", f"schedule {dec['slot']}: posting {_pretty(it['project'])}", "ok")
            break
        except HTTPException as e:
            _pq.mark(_i.PROJECTS, it["id"], status="failed", error=str(e.detail)[:300])
            refused.append(_pretty(it["project"]))
            _ev("publish", f"schedule {dec['slot']}: {_pretty(it['project'])} can't post — {e.detail} "
                           "(moved to Errors and Needs you; trying the next video)", "error")
    return dec


def _check_new_sources():
    """A source added by any route is read within 10 minutes, not at the next
    6-hourly autopilot refresh (free: one series page each)."""
    import watchlist as _wl
    done = 0
    for sx in _wl.load(_wl_root())["series"]:
        for m in sx.get("mirrors") or []:
            if m.get("last_checked") is None and m.get("support") == "supported":
                mm = _wl.refresh_mirror(_wl_root(), sx["id"], m["series_key"])
                _ev("tracker", f"{sx['title']}: new source checked — " + (
                    f"{mm.get('chapter_count')} chapters, latest ch.{mm.get('latest')}" if mm.get("status") == "ok"
                    else f"could not read it: {mm.get('error')}"), "ok" if mm.get("status") == "ok" else "warn")
                done += 1
    return done


def _demand_series():
    return [{"id": x["id"], "title": x["title"], "aliases": x.get("aliases") or [], "tier": x.get("tier")}
            for x in _wl_view()["series"]]


def _demand_pass():
    """Weekly YouTube demand check (step 6). Free (quota only); suggestions only."""
    import demand_research as _dr
    import yt_api
    if not yt_api.configured() or not _dr.due(_ingest_mod.PROJECTS) or _dr.STATUS.get("running"):
        return None
    out = _dr.run(_ingest_mod.PROJECTS, _demand_series(), yt_api.Client())
    sug = [k for k, v in out["series"].items() if v.get("differs")]
    _ev("research", f"demand research done: {len(out['series'])} series, {len(sug)} tier suggestion(s), "
                    f"{out.get('quota_used')} YouTube quota units", "ok")
    return out


def _scheduler_pass():
    _SCHED["last_run"] = time.time()
    for name, fn in (("budget", _resume_budget_paused), ("archive", _archive_sweep),
                     ("posting", _schedule_post_pass), ("demand", _demand_pass),
                     ("new sources", _check_new_sources), ("publish prep", _publish_prep_pass),
                     ("autopilot", lambda: _autopilot.tick(_ingest_mod.PROJECTS, _ap_deps()))):
        try:
            fn()
        except Exception as e:  # noqa — one failing part must not stop the rest
            _SCHED["last_error"] = f"{name}: {e}"
            print(f"[scheduler] {name} failed: {e}", flush=True)
            _ev("scheduler", f"{name} step failed: {e}", "error")


def _scheduler_loop():
    time.sleep(60)                       # let the server finish booting
    while True:
        _scheduler_pass()
        time.sleep(_autopilot.TICK_SECONDS)


@app.on_event("startup")
def _start_background_work():
    # Env switch so a local/dev server never spends money by itself.
    if os.environ.get("AUTOPILOT_SCHEDULER", "1") == "0" or _SCHED["started"]:
        return
    _SCHED["started"] = True
    try:
        _resume_interrupted()
    except Exception as e:  # noqa
        print(f"[boot] resume failed: {e}", flush=True)
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    try:
        if _rq.next_waiting(_ingest_mod.PROJECTS) or any(
                i["status"] == "rendering" for i in _rq.load(_ingest_mod.PROJECTS)["items"]):
            _rq_kick()                   # a queue left by a restart carries on
    except Exception as e:  # noqa
        print(f"[boot] render queue resume failed: {e}", flush=True)


@app.get("/api/autopilot")
def autopilot_status():
    st = _autopilot.status(_ingest_mod.PROJECTS, _ap_deps())
    st["scheduler"] = {"running": _SCHED["started"], "last_run": _SCHED["last_run"],
                       "last_error": _SCHED["last_error"]}
    st["waiting_jobs"] = {"budget_paused": len(_waiting_ingests("budget_paused")),
                          "interrupted": len(_waiting_ingests("interrupted"))}
    return st


class AutopilotSettingsIn(BaseModel):
    enabled: bool | None = None
    per_day: int | None = None
    budget_usd: float | None = None


@app.post("/api/autopilot/settings")
def autopilot_settings(body: AutopilotSettingsIn):
    try:
        before, after = _autopilot.update_settings(_ingest_mod.PROJECTS, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    if after.get("enabled") and not before.get("enabled") and _SCHED["started"]:
        threading.Thread(target=_scheduler_pass, daemon=True).start()   # start now, not in 10 min
    return {"ok": True, "before": before, "settings": after}


class AutopilotSeriesIn(BaseModel):
    series_id: str
    action: str          # pause | resume | retry


@app.post("/api/autopilot/series")
def autopilot_series(body: AutopilotSeriesIn):
    try:
        return {"ok": True, **_autopilot.set_series(_ingest_mod.PROJECTS, body.series_id, body.action)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/autopilot/check")
def autopilot_check_now():
    """Re-read every series page now (free) and run one scheduler pass."""
    _autopilot.runtime["last_refresh"] = 0.0
    threading.Thread(target=_scheduler_pass, daemon=True).start()
    return {"ok": True, "note": "checking sources — the card updates within a minute"}


class SpendCapIn(BaseModel):
    usd: float | None = None      # None = back to the Railway value


@app.post("/api/spend/cap")
def spend_cap_set(body: SpendCapIn):
    """Change the site's daily spend limit (owner, 2026-10-05). Bounded by the
    Railway ceiling MAX_DAILY_SPEND_CEILING_USD; every paid call is still
    checked against it in usage.gate."""
    try:
        before, after = usage.set_daily_cap(body.usd)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _ev("settings", f"Daily spend limit {('$%g' % before)} → {('$%g' % after)}"
                    + (" (back to the Railway value)" if body.usd is None else ""), "warn" if after > before else "info")
    return {"ok": True, **_spend_cap_view()}


def _spend_cap_view():
    o = usage.cap_override()
    d = usage.daily_summary()
    today = d.get("date") == usage._today()
    return {"limits": [
                {"name": "AI calls", "used": int(d.get("gemini_calls") or 0) if today else 0,
                 "max": usage.MAX_DAILY_GEMINI_CALLS, "var": "MAX_DAILY_GEMINI_CALLS"},
                {"name": "Voice characters", "used": int(d.get("tts_chars") or 0) if today else 0,
                 "max": usage.MAX_DAILY_TTS_CHARS, "var": "MAX_DAILY_TTS_CHARS"}],
            "paused_by_limit": len(_paused_today()),
            "cap": usage.daily_cap(), "railway_default": usage.MAX_DAILY_SPEND_USD,
            "ceiling": max(usage.MAX_DAILY_SPEND_CEILING_USD, usage.MAX_DAILY_SPEND_USD),
            "set_in_app": bool(o), "set_at": (o or {}).get("set_at")}


class QueueBackIn(BaseModel):
    project: str
    name: str


@app.post("/api/studio/queue/back")
def queue_back_to_review(body: QueueBackIn):
    """Owner, 2026-10-05: take a scheduled or failed video off the posting
    queue and back to Home → Needs you ("Watch the video") for another look.
    Its notes, SEO and thumbnail are kept; approving again re-queues it."""
    import publish_queue as _pqb
    import ingest as _i
    pdir = project_dir_for(body.project)
    pid = os.path.basename(pdir.rstrip("/"))
    name = os.path.basename(body.name)
    if not name.endswith(".mp4") or not os.path.exists(os.path.join(pdir, "exports", name)):
        raise HTTPException(404, "video not found")
    rows = [x for x in _pqb.load(_i.PROJECTS)["items"] if (x["project"], x["name"]) == (pid, name)]
    if any(x["status"] == "posting" for x in rows):
        raise HTTPException(409, "it's being posted right now — stop it from the jobs bar first")
    for x in rows:
        if x["status"] in ("queued", "failed"):
            _pqb.remove(_i.PROJECTS, x["id"])
    recs = load_reviews(pdir)
    rec = recs.get(name) or {"history": []}
    if rec.get("status") and rec["status"] != "review_pending":
        rec.setdefault("history", []).append({"status": rec["status"], "notes": rec.get("notes", ""),
                                              "at": rec.get("reviewed_at")})
    rec["status"] = "review_pending"
    rec["reviewed_at"] = time.time()
    recs[name] = rec
    save_reviews(pdir, recs)
    _ev("publish", f"{_pretty(pid)}: taken off the posting queue and back to Needs you for review")
    return {"ok": True, "review": review_state(pdir, name, rec)}


class PriorityIn(BaseModel):
    action: str                   # add | remove | top | clear | set
    series_id: str = ""
    chapters: list[str] = []
    order: list[dict] = []        # for "set": [{series_id, chapter}, ...]
    mode: str = "round_robin"     # for "mix": round_robin | by_series | random


@app.post("/api/autopilot/priority")
def autopilot_priority(body: PriorityIn):
    """The Make next list: chapters autopilot makes first, in this order
    (owner, 2026-10-05). Returns the list with each entry's state."""
    try:
        _autopilot.priority_edit(_ingest_mod.PROJECTS, body.action, body.series_id,
                                 body.chapters, body.order, body.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    st = _autopilot.status(_ingest_mod.PROJECTS, _ap_deps())
    return {"ok": True, "priority": st["priority"], "next": st["next"], "waiting": st["waiting"],
            "enabled": st["enabled"], "can_undo": st["settings"].get("priority_prev") is not None}


class AutopilotRunIn(BaseModel):
    series_id: str
    chapter: str


@app.post("/api/autopilot/run")
def autopilot_run_now(body: AutopilotRunIn):
    """Make one specific chapter now through autopilot (skips the daily
    chapter limit, never the budget or the site cap)."""
    try:
        return {"ok": True, **_autopilot.run_now(_ingest_mod.PROJECTS, _ap_deps(),
                                                 body.series_id, body.chapter)}
    except ValueError as e:
        raise HTTPException(409, str(e))


# ================================================================ SERIES RESEARCH
# Story research (owner, 2026-10-04): a sourced Series Bible for every series.
import research_service as _research


def _run_research(job_id, ids):
    usage.set_job(job_id)          # research calls show under this job in Logs / Spend
    j = JOBS[job_id]
    j.update(status="running", stage="research", total=len(ids), done=0)
    _persist_job(job_id)
    done = []
    for sid in ids:
        if JOBS[job_id].get("control") == "stop":
            j.update(status="cancelled", error="stopped by you")
            break
        j["current"] = sid
        _persist_job(job_id)
        try:
            b = _research.build(sid)
            done.append(sid)
            _ev("research", f"{b.get('canonical_title', sid)}: {(_research.STATUS.get(sid) or {}).get('summary', 'done')}", "ok")
        except usage.UsageCapExceeded as e:
            j.update(status="error", error=f"spend cap reached: {e}")
            break
        except Exception as e:  # noqa — one series failing must not stop the rest
            j.setdefault("failed", []).append({"id": sid, "error": str(e)[:200]})
            _ev("research", f"{sid}: research failed: {str(e)[:160]}", "error")
        j["done"] += 1
        _persist_job(job_id)
    if j.get("status") == "running":
        j["status"] = "done"
    j.update(ended=time.time(), researched=done)
    _persist_job(job_id)


class ResearchIn(BaseModel):
    series_id: str = ""
    series_ids: list[str] = []
    missing_only: bool = True


@app.post("/api/series/research")
def series_research_start(body: ResearchIn):
    """Research one series, or every watchlist series (missing_only: those
    without a bible). One background job, one series at a time."""
    import watchlist as _wl
    if body.series_id or body.series_ids:
        ids = list(dict.fromkeys(([body.series_id] if body.series_id else []) + list(body.series_ids)))
    else:
        ids = []
        for s in _wl.load(_wl_root())["series"]:
            if not body.missing_only or _research.needs_research(s):
                ids.append(s["id"])
    if not ids:
        return {"ok": True, "job": None, "note": "every series already has a bible"}
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"type": "research", "status": "queued", "stage": "queued", "done": 0,
                    "total": len(ids), "error": None, "ts": time.time(),
                    "project": ids[0] if len(ids) == 1 else f"{len(ids)} series"}
    _persist_job(job_id)
    threading.Thread(target=_run_research, args=(job_id, ids), daemon=True).start()
    return {"ok": True, "job": job_id, "series": ids}


@app.get("/api/series/bible")
def series_bible_view(series_id: str):
    try:
        return _research.view(series_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


class BibleIn(BaseModel):
    series_id: str
    bible: dict


@app.post("/api/series/bible")
def series_bible_save(body: BibleIn):
    if not isinstance(body.bible.get("characters", []), list):
        raise HTTPException(400, "characters must be a list")
    try:
        return {"ok": True, "bible": _research.save_owner_edit(body.series_id, body.bible)}
    except ValueError as e:
        raise HTTPException(404, str(e))


# ================================================================ SERIES BOARD
# One card per series (owner, 2026-10-04): the Release calendar and the
# Watchlist merged. "Not made yet" used to count every chapter since ch.1 and
# ignore the autopilot ledger; the board counts what is LEFT IN THE PLAN (from
# autopilot's start window, never-repeat ledger included) and shows the back
# catalogue separately. Chapter lists come per card, on open (the old view drew
# up to 360 chips per series up front).
def _made_by_series(view, st):
    import providers as _prov
    made = {}
    for sx in view:
        got = {str(c) for c in sx.get("ingested") or []}
        keys = {_prov.canonical_key(m.get("series_key", "")) for m in sx.get("mirrors") or []}
        for k, e in st["ledger"].items():
            if e.get("status") in _autopilot.MADE and (e.get("series_id") == sx["id"] or k.split("|")[0] in keys):
                got.add(str(e.get("chapter")))
        made[sx["id"]] = {_autopilot.norm_chapter(c.split(" ")[0]) for c in got if c}
    return made


@app.get("/api/series/board")
def series_board():
    deps = _ap_deps()
    view = deps["view"]()
    with _autopilot._lock:
        st = _autopilot.load(_ingest_mod.PROJECTS)
        rows = {r["series_id"]: r for r in _autopilot.candidates(
            st, view, deps["canon"], deps["live_projects"](), time.time())}
        _autopilot.save(_ingest_mod.PROJECTS, st)
    made = _made_by_series(view, st)
    out = []
    for sx in view:
        r = rows.get(sx["id"], {})
        best = next((m for m in sx.get("mirrors") or [] if m.get("series_key") == (sx.get("preferred_mirror") or sx.get("best_mirror"))),
                    (sx.get("mirrors") or [None])[0])
        chs = sorted({_autopilot.norm_chapter(c) for c in (best or {}).get("chapters") or []}, key=_autopilot.chap_key)
        start = r.get("start_from")
        mine = made.get(sx["id"], set())
        top = max((_autopilot.chap_key(c) for c in mine), default=None)
        earlier = [c for c in chs if start and _autopilot.chap_key(c) < _autopilot.chap_key(start) and c not in mine]
        latest = chs[-1] if chs else None
        rd = ((best or {}).get("release_dates") or {}).get(latest) if latest else None
        bible = None
        try:
            b = _research.existing_bible(sx)
            if b:
                bible = {"characters": len(b.get("characters") or []),
                         "researched_at": (b.get("research") or {}).get("at"),
                         "disputes": len((b.get("research") or {}).get("disputes") or []),
                         "suggested": len(b.get("suggested_characters") or [])}
        except Exception:
            pass
        out.append({
            "id": sx["id"], "title": sx["title"], "tier": sx.get("tier"), "tier_label": sx.get("tier_label"),
            "rank": sx.get("rank"), "source": (best or {}).get("label") or (best or {}).get("source"),
            "series_key": (best or {}).get("series_key"), "cover": bool((best or {}).get("cover")),
            "latest": latest, "latest_date": rd[0] if rd else None, "latest_approx": bool(rd and rd[1]),
            "made": sorted(mine, key=_autopilot.chap_key), "next": r.get("next"),
            "left_in_plan": r.get("remaining") or [], "plan_from": start,
            "earlier_not_planned": len(earlier),
            "new_since_made": [c for c in chs if top is not None and _autopilot.chap_key(c) > top],
            # owner, 2026-10-07: gaps INSIDE what was made (358 made, 361 made,
            # 359-360 not) — said plainly, not hidden in the back catalogue
            "skipped": [c for c in chs if mine and c not in mine
                        and min(_autopilot.chap_key(m) for m in mine) < _autopilot.chap_key(c) < top],
            "state": r.get("state"), "reason": r.get("reason"), "made_by_autopilot": r.get("made_by_autopilot", 0),
            "bible": bible, "checked": sx.get("checked"),
            "source_status": (best or {}).get("status"), "source_checked": (best or {}).get("last_checked"),
        })
    try:
        import demand_research as _dr
        dem = _dr.load(_ingest_mod.PROJECTS).get("series") or {}
    except Exception:
        dem = {}
    for row in out:
        dv = dem.get(row["id"])
        if dv:
            row["demand"] = {k: dv.get(k) for k in ("level", "median_vpd", "recaps", "recent", "suggested_tier", "at", "top")}
            row["demand"]["differs"] = bool(dv.get("suggested_tier") and dv["suggested_tier"] != row.get("tier"))
    return {"series": out, "autopilot": {"enabled": bool(st["settings"].get("enabled")),
                                         "paused": st["settings"].get("paused_series") or []}}


@app.get("/api/pipeline")
def pipeline_view(project: str = ""):
    """One chapter's pipeline strip (LongForm lesson, step 7)."""
    import pipeline_steps as _ps
    pdir = project_dir_for(project)
    meta = _read_json(os.path.join(pdir, "project.json"))
    return {"project": os.path.basename(pdir.rstrip("/")), "steps": _ps.status(pdir),
            "can_rerun": bool(meta.get("url")) and (meta.get("engine") or "gemini") == "gemini",
            "busy": _chapter_busy(pdir)}


def _chapter_busy(pdir):
    pid = os.path.basename(pdir.rstrip("/"))
    url = _read_json(os.path.join(pdir, "project.json")).get("url")
    import ingest as _ib
    for j in list(INGEST.values()):
        # same chapter = same project id; a saved version (-img, -v2) shares the
        # URL but is a different chapter (2026-10-04: it blocked the original)
        jp = _job_pid(j)
        if j.get("status") in ("queued", "running") and (jp == pid or (not jp and url and j.get("url") == url)):
            return "an ingest of this chapter is " + j["status"]
    for j in list(JOBS.values()):
        if j.get("status") in ("queued", "running") and j.get("project") == pid:
            return f"a {j.get('type') or 'render'} job of this chapter is {j['status']}"
    return None


# ============================================ render queue (owner, 2026-10-05)
import render_queue as _rq
_RQ = {"thread": None}
_RQ_LOCK = threading.Lock()
_RQ_POLL = 3.0


def _rq_job_alive(job_id):
    return bool(job_id) and (JOBS.get(job_id) or {}).get("status") in ("running", "queued", "paused", "pausing")


def _rq_kick():
    """Start the queue worker if it isn't running (idempotent)."""
    with _RQ_LOCK:
        t = _RQ["thread"]
        if t and t.is_alive():
            return
        _RQ["thread"] = threading.Thread(target=_rq_worker, daemon=True)
        _RQ["thread"].start()


def _rq_worker():
    """Render queued chapters one at a time, each in its own voice unless the
    owner chose a re-voice for it. Exits when the queue is empty."""
    root = _ingest_mod.PROJECTS
    _rq.recover(root, _rq_job_alive)
    while True:
        it = _rq.next_waiting(root)
        if not it:
            return
        if _render_lock():                    # a render started by hand: wait for it
            time.sleep(_RQ_POLL)
            continue
        name = _pretty(it["project"])
        try:
            activate_project(ActivateIn(id=it["project"]))
            r = storyboard_approve(ApproveIn(approved=True, keep_voice=it.get("keep_voice", True), force_start=True))
            job = r.get("job")
            if not job:
                raise RuntimeError(r.get("note") or "nothing to render")
        except Exception as e:  # noqa
            why = e.detail if isinstance(e, HTTPException) else str(e)
            _rq.update(root, it["id"], status="error", error=str(why)[:300], ended=time.time())
            _ev("render", f"Render queue: {name} couldn't start: {str(why)[:160]}", "error")
            continue
        _rq.update(root, it["id"], status="rendering", job=job, started=time.time(),
                   attempts=it.get("attempts", 0) + 1)
        _ev("render", f"Render queue: {name} started ({len(_rq.waiting(root))} waiting after it)")
        while _rq_job_alive(job):
            time.sleep(_RQ_POLL)
        j = JOBS.get(job) or {}
        ok = j.get("status") == "done"
        _rq.update(root, it["id"], status="done" if ok else "error", ended=time.time(),
                   error=None if ok else str(j.get("error") or j.get("status") or "the render stopped")[:300])


class RenderQueueIn(BaseModel):
    projects: list[str] = []
    keep_voice: bool = True       # the queue never re-voices unless asked


class RenderQueueItemIn(BaseModel):
    id: str


def _rq_view(i):
    j = JOBS.get(i.get("job") or "") or {}
    return {**i, "name": _pretty(i["project"]),
            "progress": ({k: j.get(k) for k in ("status", "stage", "done", "total", "note", "ts")}
                         if i["status"] == "rendering" else None)}


@app.get("/api/render-queue")
def render_queue_list():
    root = _ingest_mod.PROJECTS
    items = _rq.load(root)["items"]
    if any(i["status"] == "waiting" for i in items):
        _rq_kick()
    return {"items": [_rq_view(i) for i in items]}


@app.post("/api/render-queue/add")
def render_queue_add(body: RenderQueueIn):
    """Queue chapters to render, in the order given. A chapter that isn't
    ready (no matched board yet, or busy) is skipped with the reason."""
    import ingest as _i
    import pipeline_steps as _ps
    added, skipped = [], []
    for pid in dict.fromkeys(p for p in body.projects if p):
        pdir = os.path.join(_i.PROJECTS, pid)
        why = None
        if "/" in pid or ".." in pid or not os.path.exists(os.path.join(pdir, "segments.json")):
            why = "not found"
        elif next((s for s in _ps.status(pdir) if s["key"] == "match"), {}).get("state") != "done":
            why = "its board isn't finished yet"
        else:
            why = _chapter_busy(pdir)
        if not why:
            it, why = _rq.add(_i.PROJECTS, pid, keep_voice=body.keep_voice)
        if why:
            skipped.append({"id": pid, "name": _pretty(pid), "reason": why})
        else:
            added.append(pid)
    if added:
        _ev("render", f"Render queue: added {len(added)} chapter(s)"
                      + ("" if body.keep_voice else " (re-recording in the studio voice)"))
        _rq_kick()
    return {"added": added, "skipped": skipped}


@app.post("/api/render-queue/remove")
def render_queue_remove(body: RenderQueueItemIn):
    ok, why = _rq.remove(_ingest_mod.PROJECTS, body.id)
    if not ok:
        raise HTTPException(409, why)
    return {"ok": True}


@app.post("/api/render-queue/clear")
def render_queue_clear():
    return {"ok": True, "removed": _rq.clear_finished(_ingest_mod.PROJECTS)}


class PipelineRerunIn(BaseModel):
    project: str
    step: str


@app.post("/api/pipeline/rerun")
def pipeline_rerun(body: PipelineRerunIn):
    """Re-run ONE step of a chapter: clear its outputs (and what was built from
    them), then queue the chapter's ingest, which reuses everything else."""
    import ingest as _i
    import pipeline_steps as _ps
    pdir = project_dir_for(body.project)
    pid = os.path.basename(pdir.rstrip("/"))
    if body.step not in _ps.CLEARS:
        raise HTTPException(400, f"'{body.step}' cannot be re-run on its own")
    meta = _read_json(os.path.join(pdir, "project.json"))
    url = meta.get("url")
    if not url or (meta.get("engine") or "gemini") != "gemini":
        raise HTTPException(409, "only chapters made by the main (Gemini) pipeline can re-run a step")
    busy = _chapter_busy(pdir)
    if busy:
        raise HTTPException(409, busy + " — wait for it or stop it first")
    base = _i.project_id(url)
    variant = pid[len(base) + 1:] if pid.startswith(base + "-") else ""
    if _i.project_id(url, variant) != pid:
        raise HTTPException(409, "could not work out which chapter version this is")
    removed = _ps.prepare_rerun(pdir, body.step)
    if _archive.read(pdir) is not None:          # a re-run brings an archived chapter back
        _archive.unarchive(pdir)
    job = _enqueue_ingest(url, variant=variant, source="rerun",
                          why=f"re-running from: {_ps.RERUN[body.step]['label']}")
    _ev("ingest", f"{_pretty(pid)}: re-running from '{_ps.RERUN[body.step]['label']}' "
                  f"({len(removed)} file(s) cleared)")
    return {"ok": True, "job": job, "removed": len(removed)}


# ============================================ rebuild step 2: one status model
import chapter_status as _cs


def _job_pid(j):
    """A job record's project id. A FINISHED ingest stores the whole project
    dict under "project" (not just its id) — using that as a key crashed every
    chapter list with "unhashable type: dict" (2026-10-05)."""
    import ingest as _i
    p = j.get("project")
    if isinstance(p, dict):
        p = p.get("id")
    if not p and j.get("url"):
        p = _i.project_id(j["url"], j.get("variant") or "")
    return p if isinstance(p, str) else None


def _ingest_by_project():
    """Newest ingest record per project id (and per URL for ones without a folder yet)."""
    import ingest as _i
    out, by_url = {}, {}
    for jid, j in sorted(INGEST.items(), key=lambda kv: kv[1].get("ts") or 0):
        rec = dict(j, job=jid)
        pid = _job_pid(j)
        if pid:
            out[pid] = rec
        if j.get("url"):
            by_url[j["url"]] = rec
    return out, by_url


def _chapter_rows():
    """Every chapter the studio knows, with ONE status each."""
    import ingest as _i
    import autopilot as _ap
    import project_archive as _arch
    ing, _ = _ingest_by_project()
    rendering = {j.get("project") for j in JOBS.values()
                 if j.get("type") == "finalize" and j.get("status") in ("queued", "running", "paused", "pausing")}
    _close_stale_failures()
    q = _pq.load(_i.PROJECTS)["items"]
    qby = {}
    for x in q:
        qby.setdefault(x["project"], []).append(x)
    try:
        auto = {e.get("project") for e in _ap.load(_i.PROJECTS)["ledger"].values() if e.get("source") == "autopilot"}
    except Exception:
        auto = set()
    rows, seen = [], set()
    try:
        pids = sorted(p for p in os.listdir(_i.PROJECTS) if not p.startswith("_")
                      and os.path.isdir(os.path.join(_i.PROJECTS, p)))
    except OSError:
        pids = []
    for pid in pids:
        pdir = os.path.join(_i.PROJECTS, pid)
        meta = _cs.project_meta(pdir)
        if not meta and pid not in ing:
            continue
        arch = _arch.view(pdir)
        f = _cs.gather(pdir, ingest_rec=ing.get(pid), rendering=pid in rendering, queue_rows=qby.get(pid),
                       review_state=review_state, load_publishes=load_publishes, archived=arch)
        st = _cs.decide(f)
        lab = _project_label(pdir)
        ij = ing.get(pid) or {}
        if not (lab.get("title") or "").strip() or not meta.get("url"):
            # still being made: no project.json yet, so name it from its link
            # (owner, 2026-10-06: blank "Waiting" cards on Home)
            _s, _c = _i.parse_series_chapter(ij.get("url") or "")
            if _s:
                _st = _i.to_title_case(_i.clean_series_slug(_s))
                lab = {**lab, "series": lab.get("series") or _st, "chapter": lab.get("chapter") or _c,
                       "title": (lab.get("title") or "").strip() or f"{_st} Ch.{_c}"}
            meta = {**meta, "url": meta.get("url") or ij.get("url")}
        rows.append({"id": pid, "series": _cs.clean_title(lab["series"] or meta.get("series") or "", meta.get("url")),
                     "chapter": lab["chapter"],
                     "title": _cs.clean_title(lab["title"], meta.get("url")), "url": meta.get("url"), "status": st, "video": f.get("video"),
                     "auto": pid in auto, "engine": meta.get("engine") or "gemini", "archive": arch,
                     "updated": max((os.path.getmtime(os.path.join(pdir, x)) for x in ("segments.json", "project.json")
                                     if os.path.exists(os.path.join(pdir, x))), default=None),
                     "job": (ing.get(pid) or {}).get("job")})
        seen.add(pid)
    for pid, j in ing.items():                       # started, no folder yet
        if pid in seen or j.get("status") not in _cs.LIVE_INGEST + _cs.WAITING_INGEST + ("error",):
            continue
        series, chapter = _i.parse_series_chapter(j.get("url") or "")
        st = _cs.decide({"ingest_status": j.get("status"), "ingest_stage": j.get("stage"),
                         "ingest_error": j.get("error")})
        rows.append({"id": pid, "series": _i.to_title_case(_i.clean_series_slug(series or "")), "chapter": chapter,
                     "title": f"{_i.to_title_case(_i.clean_series_slug(series or ''))} Ch.{chapter}", "url": j.get("url"),
                     "status": st, "video": None, "auto": j.get("source") == "autopilot", "engine": j.get("engine"),
                     "archive": None, "updated": j.get("ts"), "job": j.get("job")})
    # each chapter's series (by its source link) and whether that series has a cover
    try:
        import watchlist as _wl
        wl = _wl.load(_wl_root())
        for r in rows:
            sx, m = _wl.find_by_mirror(wl, r["url"]) if r.get("url") else (None, None)
            r["series_id"] = sx["id"] if sx else None
            r["cover"] = bool(sx and any(mm.get("cover") for mm in sx.get("mirrors") or []))
    except Exception:
        pass
    rows.sort(key=lambda r: -(r["updated"] or 0))
    return rows


@app.get("/api/chapters")
def chapters_list(series: str = "", status: str = ""):
    rows = _chapter_rows()
    if series:
        rows = [r for r in rows if r["series"].lower() == series.lower()]
    if status:
        rows = [r for r in rows if r["status"]["key"] == status]
    counts = {}
    for r in _chapter_rows() if (series or status) else rows:
        counts[r["status"]["key"]] = counts.get(r["status"]["key"], 0) + 1
    return {"chapters": rows, "counts": counts, "statuses": [_cs.view(k) for k in _cs.ORDER]}


@app.get("/api/home")
def home_view():
    """Home: today's numbers and the things that need the owner."""
    import shutil
    import ingest as _i
    rows = _chapter_rows()
    counts = {}
    for r in rows:
        counts[r["status"]["key"]] = counts.get(r["status"]["key"], 0) + 1
    need = []
    # Owner, 2026-10-06: nothing on the Posting schedule (queued or posting)
    # appears under Needs you, whatever its board status says.
    q = _pq.sync(_i.PROJECTS, _pub_status)
    on_schedule = {x.get("project") for x in q["items"] if x.get("status") in ("queued", "posting")}
    for r in rows:
        k = r["status"]["key"]
        if r["id"] in on_schedule:
            continue
        if k in ("to_review", "video_ready", "failed", "waiting"):
            need.append({"kind": k, "id": r["id"], "title": r["title"], "status": r["status"],
                         "series": r.get("series") or "", "chapter": r.get("chapter"),
                         "series_id": r.get("series_id"), "cover": r.get("cover"), "auto": r.get("auto"),
                         "action": {"to_review": "Check the board", "video_ready": "Watch the video",
                                    "failed": "See why and retry", "waiting": "Resume"}[k]})
    # Spec 10 Phase D: a series with chapters waiting on you but no title hook
    # yet gets ONE "pick the hook" item at the top of its group.
    seen_sid = set()
    import ingest as _ingx
    for n in list(need):
        pid_ = n.get("id")
        if not pid_ or n.get("kind") not in ("to_review", "video_ready"):
            continue
        try:
            pdir_ = os.path.join(_ingx.PROJECTS, pid_)
            sid_ = _series_ident(pdir_)[0]
            if not sid_ or sid_ in seen_sid:
                continue
            seen_sid.add(sid_)
            import series_pack as _spx
            if ((_spx.load(_yt_root(), sid_) or {}).get("title_lock") or {}).get("approved_by_user"):
                continue
        except Exception:
            continue
        need.append({"kind": "hook", "id": pid_, "series": n.get("series"), "chapter": "-1",
                     "series_id": n.get("series_id"), "cover": n.get("cover"),
                     "title": f"Pick the title hook for {n.get('series')}",
                     "status": {"key": "found", "label": "Title hook", "tone": "warn",
                                "reason": "Every chapter's title becomes \"[N] hook — series | Manhwa Recap\". "
                                          "Pick the hook once; only the chapter number changes after that."},
                     "action": "Pick the hook"})
    # Owner, 2026-10-07: review a series in chapter order (ch.81 before ch.93)
    # so posting follows it — Needs you is grouped by series, chapters ascending.
    def _chn(x):
        try:
            return float(str(x.get("chapter") or "").split(" ")[0])
        except ValueError:
            return float("inf")
    need.sort(key=lambda x: ((x.get("series") or x.get("title") or "").lower(), _chn(x)))
    try:
        dem = __import__("demand_research").load(_i.PROJECTS).get("series") or {}
        tiers = {s["id"]: s.get("tier") for s in _wl_view()["series"]}
        n_sug = sum(1 for k, v in dem.items() if v.get("suggested_tier") and v["suggested_tier"] != tiers.get(k))
        if n_sug:
            # owner, 2026-10-06: "what is '3 tier suggestion(s) from demand research'?"
            need.append({"kind": "demand",
                         "title": f"YouTube demand suggests a new priority for {n_sug} series",
                         "status": {"key": "found", "label": "Suggestion", "tone": "info",
                                    "reason": "The weekly demand check compares YouTube interest in each series and "
                                              "suggests moving it to Make now, Next up or Watching. Nothing changes until you accept."},
                         "action": "See the suggestions"})
    except Exception:
        pass
    sched = _studio.load().get("schedule") or {}
    spent, cap = _ap_deps()["spend"]()
    st = _autopilot.load(_i.PROJECTS)
    try:
        du = shutil.disk_usage(_i.PROJECTS)
        disk = {"used_gb": round(du.used / 1e9, 1), "total_gb": round(du.total / 1e9, 1)}
    except OSError:
        disk = {}
    try:
        _prio = _autopilot.settings(_ingest_mod.PROJECTS).get("priority") or []
    except Exception:
        _prio = []
    # every chapter that needs you, so Home matches Library (owner, 2026-10-06:
    # the old 20-card cap hid 5 of 25)
    return {"counts": counts, "need": need[:200], "make_next": len(_prio),
            "queue": {"scheduled": sum(1 for x in q["items"] if x["status"] == "queued"),
                      "next_post": _pq.next_slot(sched, time.time()) if sched.get("enabled") else None,
                      "schedule_on": bool(sched.get("enabled"))},
            "spend": {"today": round(spent, 2), "cap": cap, "autopilot": _ap_spent_today(),
                      "autopilot_budget": st["settings"].get("budget_usd")},
            "autopilot": {"enabled": bool(st["settings"].get("enabled")), "per_day": st["settings"].get("per_day")},
            "disk": disk, "jobs": jobs_active().get("jobs", []),
            "n_series": len(_wl_view()["series"])}


@app.get("/api/library")
def library_view():
    """Library: every series with its chapters (made ones with their status,
    plus the next ones the source has)."""
    import watchlist as _wl
    board = series_board()
    rows = _chapter_rows()
    data = _wl.load(_wl_root())
    owner = {}
    for r in rows:                                   # which series each chapter belongs to, by its source link
        s_, _m = _wl.find_by_mirror(data, r["url"]) if r.get("url") else (None, None)
        if s_:
            owner[r["id"]] = s_["id"]
    import ingest as _i
    gone = _read_json(os.path.join(_i.PROJECTS, GONE_NAME)) or {}
    gone_by = {}
    for gpid, g in gone.items():
        s_, _m = _wl.find_by_mirror(data, g.get("url") or "") if g.get("url") else (None, None)
        if s_:
            gone_by[(s_["id"], _autopilot.norm_chapter(g.get("chapter") or ""))] = dict(g, project=gpid)
    out = []
    for sx in board["series"]:
        made = [r for r in rows if owner.get(r["id"]) == sx["id"]]
        have = {_autopilot.norm_chapter(str(r.get("chapter") or "")) for r in made}
        # Owner, 2026-10-07: a chapter autopilot made whose folder was removed
        # (deleted, or cleaned up after posting) is still MADE — show it as such,
        # never as "Not made" with a paid Make button.
        for c in sx.get("made") or []:
            if _autopilot.norm_chapter(c) in have:
                continue
            g = gone_by.get((sx["id"], _autopilot.norm_chapter(c))) or {}
            why = g.get("why") or "removed before removals were recorded (deleted, or cleaned up after posting)"
            posted = g.get("posted")
            made.append({"id": None, "chapter": c, "title": f"{sx['title']} ch.{c}", "gone": True,
                         "posted_url": posted if isinstance(posted, str) else None,
                         "status": {"key": "archived", "label": "Made · folder removed", "tone": "muted",
                                    "reason": ("posted, then " if posted else "") + why}})
        counts = {}
        for r in made:
            counts[r["status"]["key"]] = counts.get(r["status"]["key"], 0) + 1
        out.append({**sx, "chapters": made, "counts": counts})
    return {"series": out, "autopilot": board["autopilot"]}


@app.get("/api/chapter/{pid}")
def chapter_view(pid: str):
    """Chapter page: its status, step strip, video and publish details."""
    import pipeline_steps as _ps
    pdir = project_dir_for(pid)
    row = next((r for r in _chapter_rows() if r["id"] == pid), None)
    if row is None:
        raise HTTPException(404, "unknown chapter")
    meta = _cs.project_meta(pdir)
    out = {**row, "steps": _ps.status(pdir), "busy": _chapter_busy(pdir), "drive": _drive.record(pdir),
           "drive_ready": _drive.configured(),
           "can_rerun": bool(meta.get("url")) and (meta.get("engine") or "gemini") == "gemini"}
    rj = [dict(j, id=k) for k, j in JOBS.items() if j.get("type") == "finalize" and j.get("project") == pid]
    rj.sort(key=lambda j: j.get("ts") or 0)
    if rj:
        last = rj[-1]
        out["render_job"] = {k: last.get(k) for k in ("id", "status", "stage", "done", "total", "error", "export", "ts", "ended", "note", "keep_voice")}
    out["revoice"] = _revoice_plan(pdir)
    out["render_queue"] = _rq.position(_ingest_mod.PROJECTS, pid)
    vids = []
    ed = os.path.join(pdir, "exports")
    for f in sorted(os.listdir(ed) if os.path.isdir(ed) else [], key=lambda f: -os.path.getmtime(os.path.join(ed, f))):
        if f.endswith(".mp4"):
            st_ = os.stat(os.path.join(ed, f))
            age = (time.time() - st_.st_mtime) / 86400
            try:
                rs = review_state(pdir, f)
            except Exception:
                rs = {}
            vids.append({"name": f, "size_mb": round(st_.st_size / 1e6, 1), "at": st_.st_mtime,
                         "expires_in_days": max(0, round(EXPORT_RETENTION_DAYS - age, 1)),
                         "review": rs.get("status"), "superseded": rs.get("superseded"),
                         "phone_ok": _phone_ok(os.path.join(ed, f)),
                         "phone_fix": _phonefix_job(pid, f)})
    out["videos"] = vids
    ing, _u = _ingest_by_project()
    ij = ing.get(pid)
    if ij and ij.get("status") in ("queued", "running", "paused", "pausing", "budget_paused", "interrupted", "error"):
        out["ingest"] = {k: ij.get(k) for k in ("job", "status", "stage", "pct", "msg", "error", "ts", "source")}
    # a "send back" verdict must reach the board with its notes (classic banner)
    for v in vids:
        if v.get("review") == "sent_back":
            out["sent_back"] = {"video": v["name"], "notes": (load_reviews(pdir).get(v["name"]) or {}).get("notes", "")}
            break
    out["built_with"] = {"match": meta.get("match_method"), "engine": meta.get("engine") or "gemini",
                         "split": (meta.get("split_coverage") or {}).get("engine") or (meta.get("split_coverage") or {}).get("format"),
                         "pages": meta.get("n_pages"), "scrape_warning": meta.get("scrape_warning") or None}
    if row.get("video"):
        name = row["video"]
        store = load_publish(pdir)
        out["publish"] = {"metadata": {**publish_defaults(pdir), **(store.get(name) or {})},
                          "record": load_publishes(pdir).get(name),
                          "review": review_state(pdir, name),
                          "url": f"/export/{name}?project={pid}"}
    return out


@app.get("/api/qa/chapters")
def api_qa_chapters():
    """Read-only identity QA over every chapter (chapter_qa.py): its pictures
    are its own, its pages aren't a neighbour's, its video matches its cut."""
    import chapter_qa as _q
    import ingest as _ing
    cache, out = {}, []
    for m in _ing.list_projects():
        pid = m.get("id") or ""
        if not pid or not os.path.exists(os.path.join(_ing.PROJECTS, pid, "segments.json")):
            continue
        exp = None
        url = m.get("url") or ""
        if url and "/" in url:
            try:
                exp = _ing.project_id(url, m.get("variant") or "")
            except Exception:
                exp = None
        try:
            out.append(_q.check(_ing.PROJECTS, pid, exp, cache))
        except Exception as e:  # noqa — one broken folder never hides the rest
            out.append({"project": pid, "status": "bad", "issues": [f"check failed: {str(e)[:160]}"]})
    order = {"bad": 0, "warn": 1, "ok": 2}
    out.sort(key=lambda r: (order.get(r["status"], 3), r["project"]))
    return {"checked": len(out), "bad": sum(r["status"] == "bad" for r in out),
            "warn": sum(r["status"] == "warn" for r in out), "chapters": out}


@app.get("/api/board/{pid}")
def board_view(pid: str, activate: int = 1):
    """The Chapter page's board as data (board_data.py). Board edits act on the
    ACTIVE chapter, so opening a chapter here makes it active (activate=1)."""
    import board_data
    import matcher
    pdir = project_dir_for(pid)
    locked = None
    if activate and get_active_project_id() != pid and os.path.exists(os.path.join(pdir, "segments.json")):
        try:
            activate_project(ActivateIn(id=pid))
        except HTTPException as e:
            if e.status_code != 409:
                raise
            locked = e.detail             # shown read-only until the render ends
    review = _load_reviews_side(pdir)
    out = board_data.build(pdir, review, junk_reason=matcher.junk_reason)
    try:
        approved = bool(json.load(open(os.path.join(pdir, "storyboard.json"))).get("approved"))
    except (OSError, ValueError):
        approved = False
    out["rendered_once"] = approved
    out["active"] = get_active_project_id() == pid
    out["locked"] = locked
    out["project"] = pid                      # media URLs name this chapter
    return out


# ============================================ rebuild step 6: Google Drive copy
import drive_store as _drive
_DRIVE_RUNNING = set()


def _drive_copy_later(pdir, name):
    """Copy a finished export to Drive in the background (never blocks or fails the render)."""
    if not name or not _drive.configured():
        return
    threading.Thread(target=_drive_copy, args=(pdir, name), daemon=True).start()


def _drive_copy(pdir, name):
    import thumbnail as _tb
    pid = os.path.basename(pdir.rstrip("/"))
    if pid in _DRIVE_RUNNING:
        return None
    _DRIVE_RUNNING.add(pid)
    try:
        lab = _project_label(pdir)
        series = _cs.clean_title(lab["series"] or "", _cs.project_meta(pdir).get("url"))
        try:
            thumb = _tb.path_for(pdir, name) or None
        except Exception:
            thumb = None
        rec = _drive.copy_chapter(pdir, name, series, lab["chapter"], thumb_path=thumb)
        msg = f"{_pretty(pid)}: copied to Drive ({series} / Ch {lab['chapter']})"
        # free the render clips only when this is still the chapter's newest video and nothing is rendering it
        rendering = any(j.get("type") == "finalize" and j.get("project") == pid and j.get("status") in ("queued", "running")
                        for j in JOBS.values())
        latest = _cs._latest_export(pdir)
        if latest == name and not rendering:
            mb = _drive.free_clips(pdir)
            if mb:
                msg += f" · freed {mb} MB of render clips"
        _ev("drive", msg, "ok")
        return rec
    except Exception as e:  # noqa — a Drive problem never costs the video
        _ev("drive", f"{_pretty(pid)}: Drive copy failed — {str(e)[:220]}", "error")
        return None
    finally:
        _DRIVE_RUNNING.discard(pid)


@app.get("/api/drive/status")
def drive_status():
    import ingest as _i
    return _drive.status(_i.PROJECTS)


class DriveCopyIn(BaseModel):
    project: str


@app.post("/api/drive/copy")
def drive_copy_now(body: DriveCopyIn):
    """Copy (or re-copy) a chapter's newest video to Drive now."""
    pdir = project_dir_for(body.project)
    if not _drive.configured():
        raise HTTPException(409, "Drive is not set up: GOOGLE_SERVICE_ACCOUNT_JSON and the folder id are needed on Railway")
    name = _cs._latest_export(pdir)
    if not name:
        raise HTTPException(404, "this chapter has no video to copy")
    if os.path.basename(pdir.rstrip("/")) in _DRIVE_RUNNING:
        raise HTTPException(409, "a copy of this chapter is already running")
    _drive_copy_later(pdir, name)
    return {"ok": True, "video": name}


@app.get("/api/critique")
def critique_view(project: str = ""):
    """What the narration's second pass (the script editor) flagged and rewrote."""
    pdir = project_dir_for(project)
    try:
        with open(os.path.join(pdir, "critique.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"missing": True}


@app.get("/api/demand")
def demand_view():
    import demand_research as _dr
    import yt_api
    d = _dr.load(_ingest_mod.PROJECTS)
    return {**d, "status": dict(_dr.STATUS), "configured": yt_api.configured(),
            "every_days": _dr.EVERY_DAYS}


@app.post("/api/demand/run")
def demand_run():
    import demand_research as _dr
    import yt_api
    if not yt_api.configured():
        raise HTTPException(409, "no YouTube API key is configured")
    if _dr.STATUS.get("running"):
        raise HTTPException(409, "demand research is already running")
    series = _demand_series()

    def work():
        try:
            out = _dr.run(_ingest_mod.PROJECTS, series, yt_api.Client())
            _ev("research", f"demand research done: {len(out['series'])} series, "
                            f"{out.get('quota_used')} YouTube quota units", "ok")
        except Exception as e:  # noqa
            _dr.STATUS["error"] = str(e)[:300]
            _ev("research", f"demand research failed: {e}", "error")
    threading.Thread(target=work, daemon=True).start()
    _ev("research", f"demand research started for {len(series)} series")
    return {"ok": True, "series": len(series)}


@app.get("/api/series/chapters")
def series_chapters(series_id: str):
    """One series' chapters, newest first, with date and made — for its card."""
    view = _wl_view()["series"]
    sx = next((x for x in view if x["id"] == series_id), None)
    if sx is None:
        raise HTTPException(404, "unknown series")
    st = _autopilot.load(_ingest_mod.PROJECTS)
    mine = _made_by_series([sx], st)[series_id]
    best = next((m for m in sx.get("mirrors") or [] if m.get("series_key") == (sx.get("preferred_mirror") or sx.get("best_mirror"))),
                (sx.get("mirrors") or [None])[0]) or {}
    dates = best.get("release_dates") or {}
    chs = sorted({_autopilot.norm_chapter(c) for c in best.get("chapters") or []}, key=_autopilot.chap_key, reverse=True)
    return {"series_id": series_id, "series_key": best.get("series_key"),
            "chapters": [{"ch": c, "date": (dates.get(c) or [None])[0], "approx": bool((dates.get(c) or [0, 0])[1]),
                          "made": c in mine} for c in chs]}


class BackfillIn(BaseModel):
    series_id: str
    from_chapter: str


@app.post("/api/autopilot/backfill")
def autopilot_backfill(body: BackfillIn):
    """Plan a series' back catalogue from ch.N: autopilot then makes N, N+1, …
    in story order (before newer chapters of THAT series). Owner rule: never
    by default, only through this button."""
    view = _wl_view()["series"]
    sx = next((x for x in view if x["id"] == body.series_id), None)
    if sx is None:
        raise HTTPException(404, "unknown series")
    ch = _autopilot.norm_chapter(body.from_chapter)
    listed = {_autopilot.norm_chapter(c) for m in sx.get("mirrors") or [] for c in m.get("chapters") or []}
    if ch not in listed:
        raise HTTPException(400, f"ch.{ch} is not listed on the source")
    with _autopilot._lock:
        st = _autopilot.load(_ingest_mod.PROJECTS)
        before = st["start_from"].get(body.series_id)
        st["start_from"][body.series_id] = ch
        _autopilot.save(_ingest_mod.PROJECTS, st)
    _autopilot.audit("backfill_planned", series=body.series_id, before=before, after=ch)
    return {"ok": True, "series_id": body.series_id, "plan_from": ch, "before": before}


# ================================================================ LOGS (step 2)
# Owner, 2026-10-04: Logs like Scrapper's — a live feed, a jobs bar on every
# page, jobs with real names and costs, and spend in one place.
import events as _events

_COST_CACHE = {"key": None, "by_job": {}, "by_day": {}, "by_kind": {}}


def _usage_index():
    """Per-job and per-day spend from the usage log, cached by file size."""
    try:
        st = os.stat(usage.LOG_PATH)
        key = (st.st_size, st.st_mtime)
    except OSError:
        return _COST_CACHE
    if _COST_CACHE["key"] == key:
        return _COST_CACHE
    by_job, by_day, by_kind = {}, {}, {}
    with open(usage.LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            c = e.get("est_cost_usd") or 0.0
            jid = e.get("job_id") or "?"
            by_job[jid] = by_job.get(jid, 0.0) + c
            try:
                day = _autopilot.et_day(datetime.fromisoformat(e["ts"]).timestamp())
            except Exception:
                continue
            by_day[day] = by_day.get(day, 0.0) + c
            k = (day[:7], e.get("provider") or e.get("kind") or "?")
            by_kind[k] = by_kind.get(k, 0.0) + c
    _COST_CACHE.update(key=key, by_job=by_job, by_day=by_day, by_kind=by_kind)
    return _COST_CACHE


@app.get("/api/events")
def events_feed(after: int = 0, limit: int = 300, kind: str = ""):
    ev = _events.since(after, min(max(limit, 1), 1000), kind or None)
    return {"events": ev, "last": ev[-1]["id"] if ev else after}


_LIVE = ("running", "queued", "paused", "pausing")
_WAITING = ("budget_paused", "interrupted", "held")


def _job_rows():
    import ingest as _i
    projects = set(os.listdir(_i.PROJECTS)) if os.path.isdir(_i.PROJECTS) else set()
    cost = _usage_index()["by_job"]
    rows = []
    for x in _all_ingest_jobs():
        if str(x.get("job", "")).startswith("render_") or x.get("type"):
            continue          # render/research records share the folder; listed below
        url = x.get("url") or ""
        pid = (x.get("project") or {}).get("id") if isinstance(x.get("project"), dict) else None
        pid = pid or (_i.project_id(url, x.get("variant", "")) if "://" in url else None)
        kind = "export" if url.startswith("/export") else ("autopilot" if x.get("source") == "autopilot" else "ingest")
        rows.append({"id": x["job"], "kind": kind, "name": _pretty(url, x.get("variant", "")) if "://" in url else (url or "(no chapter recorded)"),
                     "project": pid, "status": x.get("status") or "queued", "stage": x.get("stage"),
                     "pct": x.get("pct"), "msg": x.get("msg"), "error": x.get("error"),
                     "ts": x.get("ts") or 0, "ended": x.get("ended"), "cost": round(cost.get(x["job"], 0.0), 3),
                     "deleted": bool(pid) and pid not in projects and x.get("status") == "done"})
    disk = {}
    try:
        for fn in os.listdir(_jobs_dir()):
            if fn.startswith("render_") and fn.endswith(".json"):
                try:
                    disk[fn[7:-5]] = json.load(open(os.path.join(_jobs_dir(), fn)))
                except (OSError, ValueError):
                    pass
    except OSError:
        pass
    disk.update(JOBS)
    for jid, x in disk.items():
        t = x.get("type") or x.get("kind") or "render"
        proj = x.get("project") or ""
        rows.append({"id": jid, "kind": t, "name": _pretty(proj) if proj and "series" not in str(proj) else (proj or t),
                     "project": proj, "status": x.get("status") or "queued", "stage": x.get("stage"),
                     "pct": (round(100 * x.get("done", 0) / x["total"]) if x.get("total") else None),
                     "msg": x.get("note") or (f"{x.get('done', 0)}/{x.get('total')}" if x.get("total") else ""),
                     "error": x.get("error"), "ts": x.get("ts") or x.get("heartbeat") or 0, "ended": x.get("ended"),
                     "cost": round(cost.get(jid, 0.0), 3), "export": x.get("export"),
                     "deleted": bool(proj) and "series" not in str(proj) and proj not in projects})
    now = time.time()
    for r in rows:
        r["elapsed"] = round((r["ended"] or now) - r["ts"]) if r["ts"] else None
    rows.sort(key=lambda r: r["ts"] or 0, reverse=True)
    return rows


@app.get("/api/logs/jobs")
def logs_jobs(limit: int = 80):
    rows = _job_rows()
    day = _autopilot.et_day()
    groups = {"running": [], "waiting": [], "today": [], "earlier": []}
    for r in rows:
        if r["status"] in _LIVE:
            groups["running"].append(r)
        elif r["status"] in _WAITING:
            groups["waiting"].append(r)
        elif r["ts"] and _autopilot.et_day(r["ts"]) == day:
            groups["today"].append(r)
        else:
            groups["earlier"].append(r)
    groups["earlier"] = groups["earlier"][:limit]
    return groups


@app.get("/api/jobsbar")
def jobs_active(failed: int = 0):
    """Light: in-memory only, for the jobs bar on every page."""
    out = []
    for jid, x in list(INGEST.items()):
        if x.get("status") in _LIVE + _WAITING:
            url = x.get("url") or ""
            out.append({"id": jid, "kind": "autopilot" if x.get("source") == "autopilot" else "ingest",
                        "name": _pretty(url, x.get("variant", "")), "status": x.get("status"),
                        "stage": x.get("stage"), "pct": x.get("pct"), "msg": x.get("msg"),
                        "elapsed": round(time.time() - (x.get("ts") or time.time()))})
    for jid, x in list(JOBS.items()):
        if x.get("status") in _LIVE:
            out.append({"id": jid, "kind": x.get("type") or x.get("kind") or "render",
                        "name": _pretty(x.get("project") or "") if x.get("project") else (x.get("type") or "job"),
                        "status": x.get("status"), "stage": x.get("stage"),
                        "pct": round(100 * x.get("done", 0) / x["total"]) if x.get("total") else None,
                        "msg": x.get("note") or "", "elapsed": round(time.time() - (x.get("ts") or time.time()))})
    # ?failed=1 (new studio): a render that failed in the last 2 hours stays
    # on the bar, so a failure that happens in a second is still seen
    # (owner, 2026-10-05). The classic bar doesn't ask, so it is unchanged.
    if failed:
        words = {"revoice": "re-recording the voice", "render": "rendering clips", "export": "exporting", "queued": "starting"}
        for o in out:
            x = JOBS.get(o["id"]) or {}
            if x.get("type") == "finalize":
                o["project"] = x.get("project")
                o["stage"] = words.get(x.get("stage"), x.get("stage"))
                if x.get("total") and x.get("stage") in ("revoice", "render"):
                    o["msg"] = f"{x.get('done', 0)} of {x['total']}"
                elif x.get("stage") == "export":
                    o["msg"] = ""
        for n, it in enumerate(_rq.waiting(_ingest_mod.PROJECTS), 1):
            out.append({"id": it["id"], "kind": "render", "name": _pretty(it["project"]), "project": it["project"],
                        "status": "in_queue", "stage": None, "pct": None,
                        "msg": "next" if n == 1 else f"#{n} in line", "elapsed": None})
    for jid, x in list(JOBS.items()) if failed else []:
        if (x.get("type") == "finalize" and x.get("status") == "error"
                and time.time() - (x.get("ended") or x.get("ts") or 0) < 7200):
            out.append({"id": jid, "kind": "render", "name": _pretty(x.get("project") or ""),
                        "project": x.get("project"), "status": "error", "stage": x.get("stage"),
                        "pct": None, "msg": str(x.get("error") or "")[:240], "elapsed": None})
    return {"jobs": out}


@app.get("/api/spend")
def spend_summary():
    idx = _usage_index()
    day = _autopilot.et_day()
    month = day[:7]
    days = sorted((d, round(v, 3)) for d, v in idx["by_day"].items() if d.startswith(month))
    spent, cap = _ap_deps()["spend"]()
    st = _autopilot.load(_ingest_mod.PROJECTS)
    chapters = [{"name": _pretty(e.get("project") or ""), "cost": e.get("cost"), "at": e.get("updated_at")}
                for e in sorted(st["ledger"].values(), key=lambda e: e.get("updated_at") or 0, reverse=True)
                if e.get("cost")][:15]
    return {"day": day, "today": {"spent": round(spent, 3), "cap": cap},
            "autopilot": {"spent": _ap_spent_today(), "budget": st["settings"].get("budget_usd")},
            "month": {"label": month, "days": days, "total": round(sum(v for _, v in days), 2),
                      "by_provider": {k[1]: round(v, 3) for k, v in idx["by_kind"].items() if k[0] == month}},
            "chapters": chapters,
            "rates": usage.rate_card(),
            "note": "Measured from real tokens since 2026-10-03; earlier Gemini Flash calls were under-counted. "
                    "Google billing is the ground truth."}


# ================================================================ SETTINGS & CHANNELS (step 3)
@app.get("/api/settings/overview")
def settings_overview():
    import shutil
    import gemini_tts
    import worklog
    import ingest as _i
    st = _autopilot.load(_ingest_mod.PROJECTS)
    try:
        du = shutil.disk_usage(_i.PROJECTS)
        disk = {"used_gb": round(du.used / 1e9, 2), "total_gb": round(du.total / 1e9, 2)}
    except OSError:
        disk = {}
    sizes = []
    for m in _i.list_projects():
        pd = os.path.join(_i.PROJECTS, m.get("id") or "")
        tot = 0
        for rt, _d, fs in os.walk(pd):
            for fn in fs:
                try:
                    tot += os.path.getsize(os.path.join(rt, fn))
                except OSError:
                    pass
        sizes.append({"id": m.get("id"), "name": _pretty(m.get("id") or ""), "mb": round(tot / 1e6)})
    sizes.sort(key=lambda x: -x["mb"])
    try:
        pub = os_status()
    except Exception as e:  # noqa
        pub = {"state": "error", "detail": str(e)[:200], "accounts": []}
    spent, cap = _ap_deps()["spend"]()
    return {
        "connection": {"commit": worklog.deployed_commit(), "scheduler": {"running": _SCHED["started"], "last_run": _SCHED["last_run"],
                                                                        "last_error": _SCHED["last_error"]},
                       "now": time.time()},
        "spending": {"today": round(spent, 2), "cap": cap, "autopilot_spent": _ap_spent_today(),
                     "autopilot_budget": st["settings"].get("budget_usd"),
                     "prices_read": usage.GEMINI_PRICES_READ, **_spend_cap_view()},
        "autopilot": {"enabled": st["settings"].get("enabled"), "per_day": st["settings"].get("per_day"),
                      "window": st["settings"].get("window"),
                      "model": os.environ.get("PIPELINE_MODEL", "gemini-3.8-flash"),
                      "tier": os.environ.get("AUTOPILOT_TIER", "flex")},
        "channels": {"status": {k: pub.get(k) for k in ("state", "detail", "backend")},
                     "accounts": [{k: a.get(k) for k in ("account_id", "network", "username", "active")}
                                  for a in pub.get("accounts") or []],
                     "defaults": _studio.publish_defaults()},
        "voice": gemini_tts.load_default(_i.PROJECTS) or {},
        "export": {"speed": _studio.export_speed(), "env_override": bool(os.environ.get("EXPORT_SPEED"))},
        "title_template": _studio.title_template(),
        "music_credit": _studio.load().get("music_credit") or "",
        "drive": __import__("drive_store").status(_i.PROJECTS),
        "schedule": {**(_studio.load().get("schedule") or {}),
                     "next": _pq.next_slot(_studio.load().get("schedule") or {}, time.time())},
        "storage": {"disk": disk, "projects": sizes[:8], "n_projects": len(sizes),
                    "exports_kept_days": EXPORT_RETENTION_DAYS, "archive_days": _archive.ARCHIVE_DAYS},
    }


# ---------------------------------------------- 📺 Publishing Studio (step 4)
import publish_queue as _pq


def _yt_id(url):
    import re as _re
    m = _re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def _pub_status(project, name):
    try:
        return load_publishes(project_dir_for(project)).get(name)
    except HTTPException:
        return None


@app.get("/api/studio")
def studio_overview():
    """Every export sorted into the studio's lanes: waiting for review, ready to
    post, queued, published (plus failed posts, which go back to Ready)."""
    import ingest as _i
    import thumbnail as _tb
    _close_stale_failures()
    q = _pq.sync(_i.PROJECTS, _pub_status)
    active = {(x["project"], x["name"]): x for x in q["items"] if x["status"] in _pq.ACTIVE}
    exports = list_exports()
    review, ready, queue_rows = [], [], []
    by_key = {}
    for e in exports["exports"]:
        pdir = os.path.join(_i.PROJECTS, e["project"])
        md = {**publish_defaults(pdir), **(load_publish(pdir).get(e["name"]) or {})}
        rec = load_publishes(pdir).get(e["name"]) or {}
        done = [r for r in rec.get("results") or [] if r.get("status") == "published"]
        try:
            has_thumb = bool(_tb.path_for(pdir, e["name"]))
        except Exception:
            has_thumb = False
        row = {"project": e["project"], "name": e["name"], "series": e["series"], "chapter": e["chapter"],
               "label": e["title"], "duration": e["duration"], "created": e["created"], "mtime": e["mtime"],
               "expires_in_days": e["expires_in_days"], "url": e["url"], "review_url": e["review_url"],
               "review_status": e["review_status"], "superseded": e["superseded"],
               "title": md.get("title"), "privacy": md.get("privacy") or "private",
               "targets": md.get("targets") or [],
               "thumb": f"/thumbnail?project={e['project']}&name={e['name']}" if has_thumb else None,
               "last_error": rec.get("error") if rec.get("status") == "failed" else None}
        by_key[(e["project"], e["name"])] = row
        if done or (e["project"], e["name"]) in active:
            continue
        if e["review_status"] == "approved" and not e["superseded"]:
            ready.append(row)
        else:
            review.append(row)
    sched = _studio.load().get("schedule") or {}
    # A queued video that would be refused gets no planned time and says why,
    # so the schedule shows it before its slot comes (owner, 2026-10-07).
    blocked = {}
    for x in q["items"]:
        if x["status"] == "queued":
            try:
                b = _local_post_blockers(x["project"], x["name"])
            except Exception:
                b = []
            if b:
                blocked[x["id"]] = "; ".join(b)
    try:
        plan = _pq.planned(sched, q, time.time(),
                           lambda x: [] if x["id"] in blocked else _targets_of(x))
    except Exception:
        plan = {}
    for x in q["items"]:
        if x["status"] in _pq.ACTIVE or (x["status"] == "failed" and time.time() - x["updated_at"] < 7 * 86400):
            r = dict(by_key.get((x["project"], x["name"])) or
                     {"project": x["project"], "name": x["name"], "label": x["name"], "missing": True})
            r.update(qid=x["id"], qstatus=x["status"], qerror=x.get("error"), added_at=x["added_at"],
                     planned=plan.get(x["id"]), blocked=blocked.get(x["id"]))
            queue_rows.append(r)
    return {"review": review, "ready": ready, "queue": queue_rows, "published": _studio_published(),
            "retention_days": exports["retention_days"],
            "can_undo_order": bool(q.get("prev_order")),
            "undo_label": _pq.undo_label(q),
            "schedule": {"enabled": bool(sched.get("enabled")), "times": sched.get("times"),
                         "per_channel_per_day": sched.get("per_channel_per_day"),
                         "next": _pq.next_slot(sched, time.time()),
                         "last": (q.get("slots_done") or {})},
            "yt_stats": __import__("yt_api").configured()}


def _studio_published():
    import ingest as _i
    out = []
    try:
        pids = [p for p in os.listdir(_i.PROJECTS) if not p.startswith("_")]
    except OSError:
        pids = []
    for pid in pids:
        pdir = os.path.join(_i.PROJECTS, pid)
        if not os.path.isdir(pdir):
            continue
        pubs = load_publishes(pdir)
        if not pubs:
            continue
        label = _project_label(pdir)
        store = load_publish(pdir)
        for name, rec in pubs.items():
            ok = [r for r in rec.get("results") or [] if r.get("status") == "published"]
            if not ok:
                continue
            md = store.get(name) or {}
            out.append({"project": pid, "name": name, "label": label["title"], "series": label["series"],
                        "chapter": label["chapter"], "title": md.get("title") or label["title"],
                        "privacy": md.get("privacy") or "private",
                        "at": max((r.get("published_at") or 0) for r in ok) or rec.get("ended_at"),
                        "posts": [{"account_id": r.get("account_id"), "username": r.get("username"),
                                   "url": _post_url({"post_url": r.get("url"), "platform_post_id": r.get("platform_post_id")},
                                                    r.get("network") or "youtube"),
                                   "video_id": _yt_id(r.get("url")) or (r.get("platform_post_id")
                                                                       if (r.get("network") or "youtube") == "youtube" else None)}
                                  for r in ok],
                        "partial": len(ok) < len(rec.get("results") or [])})
    out.sort(key=lambda x: -(x["at"] or 0))
    return out


class StudioQueueIn(BaseModel):
    items: list[dict]
    targets: list[str] | None = None
    privacy: str | None = None


@app.post("/api/studio/queue")
def studio_queue_add(body: StudioQueueIn):
    """Send approved exports to the posting queue. Channels/privacy picked once
    are saved onto each video's publish details (its own page can still change them)."""
    import ingest as _i
    if body.privacy is not None and body.privacy not in ("private", "unlisted", "public"):
        raise HTTPException(400, "privacy must be private, unlisted or public")
    entries = []
    for it in body.items[:50]:
        pdir = project_dir_for(str(it.get("project") or ""))
        name = os.path.basename(str(it.get("name") or ""))
        if not name.endswith(".mp4") or not os.path.exists(os.path.join(pdir, "exports", name)):
            raise HTTPException(404, f"no export {name!r} in {it.get('project')!r}")
        rv = review_state(pdir, name)
        if rv["status"] != "approved" or rv["superseded"]:
            raise HTTPException(409, f"{name} is not approved in Video review yet")
        if body.targets is not None or body.privacy is not None:
            store = load_publish(pdir)
            md = {**publish_defaults(pdir), **(store.get(name) or {})}
            if body.targets is not None:
                md["targets"] = [t for t in body.targets if isinstance(t, str)]
            if body.privacy is not None:
                md["privacy"] = body.privacy
            store[name] = md
            save_publish(pdir, store)
        entries.append({"project": os.path.basename(pdir.rstrip("/")), "name": name})
    added, skipped = _pq.add(_i.PROJECTS, entries)
    for a in added:
        _ev("publish", f"queued for posting: {_pretty(a['project'])}")
    return {"ok": True, "added": added, "skipped": skipped}


class StudioItemIn(BaseModel):
    id: str


@app.post("/api/studio/queue/remove")
def studio_queue_remove(body: StudioItemIn):
    import ingest as _i
    try:
        it = _pq.remove(_i.PROJECTS, body.id)
    except KeyError:
        raise HTTPException(404, "no such queue item")
    except ValueError as e:
        raise HTTPException(409, str(e))
    _ev("publish", f"taken off the posting queue: {_pretty(it['project'])}")
    return {"ok": True, "item": it}


class StudioOrderIn(BaseModel):
    ids: list[str]


@app.post("/api/studio/queue/reorder")
def studio_queue_reorder(body: StudioOrderIn):
    import ingest as _i
    return {"ok": True, "order": _pq.reorder(_i.PROJECTS, body.ids)}


@app.post("/api/studio/queue/post")
def studio_queue_post(body: StudioItemIn):
    """Post one queued video now (the same checked publish job as Video review)."""
    import ingest as _i
    it = next((x for x in _pq.load(_i.PROJECTS)["items"] if x["id"] == body.id), None)
    if it is None:
        raise HTTPException(404, "no such queue item")
    if it["status"] not in ("queued", "failed"):
        raise HTTPException(409, f"this item is {it['status']}")
    r = os_publish(PublishNowIn(project=it["project"], name=it["name"]))
    sched = _studio.load().get("schedule") or {}
    _pq.ensure_posting(_i.PROJECTS, it["project"], it["name"], r.get("job"),
                       day=_pq._local(time.time(), sched.get("tz")).strftime("%Y-%m-%d"))
    _ev("publish", f"posting {_pretty(it['project'])} ({r.get('privacy')})")
    return {"ok": True, "job": r.get("job"), "privacy": r.get("privacy")}


class QueueShuffleIn(BaseModel):
    mode: str = "round_robin"


def _series_of_row(x):
    try:
        return _project_label(project_dir_for(x["project"]))["series"] or x["project"].rsplit("_", 1)[0]
    except Exception:
        return x["project"].rsplit("_", 1)[0]


@app.post("/api/studio/queue/shuffle")
def studio_queue_shuffle(body: QueueShuffleIn):
    """🔀 Mix & Shuffle the scheduled videos (Scrapper's SocialPilot), with Undo."""
    import ingest as _i
    try:
        ids = _pq.shuffle(_i.PROJECTS, body.mode, _series_of_row)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _ev("publish", f"posting order mixed ({body.mode.replace('_', ' ')}, {len(ids)} videos)")
    return {"ok": True, "order": ids}


@app.post("/api/studio/queue/undo")
def studio_queue_undo():
    import ingest as _i
    try:
        _pq.undo_order(_i.PROJECTS)
    except ValueError as e:
        raise HTTPException(409, str(e))
    _ev("publish", "posting order: last change undone")
    return {"ok": True}


@app.post("/api/studio/queue/top")
def studio_queue_top(body: StudioItemIn):
    """⏫ Post next: first in line, so it takes the next posting time."""
    import ingest as _i
    try:
        it = next((x for x in _pq.load(_i.PROJECTS)["items"] if x["id"] == body.id), None)
        _pq.to_top(_i.PROJECTS, body.id, _pretty(it["project"]) if it else "")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


class StudioBulkIn(BaseModel):
    action: str                    # approve | back | remove | post_now | channels | seo | requeue | edit
    items: list[dict]              # [{project, name, qid?}]
    targets: list[str] = []        # for "channels"
    # for "edit" (Scrapper's Mass Edit): only the fields given are changed
    title: str | None = None
    description: str | None = None
    tags: list[str] | str | None = None
    privacy: str | None = None


def _redo_seo(project, name):
    """New suggestions with the current research and title format, applied to
    title, description and tags (the owner asked for it explicitly)."""
    try:
        api_seo_generate(SeoGenIn(project=project, name=name))
        for field in ("title", "description", "tags"):
            try:
                api_seo_apply(SeoApplyIn(project=project, name=name, field=field))
            except HTTPException:
                pass
        _ev("publish", f"{_pretty(project)}: SEO redone — title, description and tags refreshed", "ok")
    except Exception as e:  # noqa
        _ev("publish", f"{_pretty(project)}: SEO redo failed — {str(e)[:150]}", "warn")


@app.post("/api/studio/bulk")
def studio_bulk(body: StudioBulkIn):
    """Do one thing to many videos at once (SocialPilot's bulk bar). Each item
    gets its own result; one failure never stops the rest."""
    if body.action not in ("approve", "back", "remove", "post_now", "channels", "seo", "requeue", "edit"):
        raise HTTPException(400, "action must be approve, back, remove, post_now, channels, seo, requeue or edit")
    if body.action == "edit" and body.privacy is not None and body.privacy not in YT_PRIVACY:
        raise HTTPException(400, f"privacy must be one of {', '.join(YT_PRIVACY)}")
    done, failed = [], []
    for it in body.items[:100]:
        pid, name, qid = it.get("project"), it.get("name"), it.get("qid")
        try:
            if body.action == "approve":
                pdir = project_dir_for(pid)
                notes = (load_reviews(pdir).get(os.path.basename(name or "")) or {}).get("notes", "")
                api_review_save(ReviewIn(project=pid, name=name, status="approved", notes=notes))
            elif body.action == "back":
                queue_back_to_review(QueueBackIn(project=pid, name=name))
            elif body.action == "remove":
                import ingest as _i
                _pq.remove(_i.PROJECTS, qid)
            elif body.action == "post_now":
                studio_queue_post(StudioItemIn(id=qid))
            elif body.action == "channels":
                pdir = project_dir_for(pid)
                store = load_publish(pdir)
                md = {**publish_defaults(pdir), **(store.get(name) or {})}
                md["targets"] = [t for t in body.targets if isinstance(t, str)]
                store[name] = md
                save_publish(pdir, store)
            elif body.action == "seo":
                threading.Thread(target=_redo_seo, args=(pid, name), daemon=True).start()
            elif body.action == "requeue":
                # Scrapper's "back to the line": a failed post waits for the next free slot
                import ingest as _i
                if not qid or not _pq.requeue(_i.PROJECTS, qid):
                    raise HTTPException(409, "only a failed or removed video can go back in line")
            elif body.action == "edit":
                pdir = project_dir_for(pid)
                store = load_publish(pdir)
                md = {**publish_defaults(pdir), **(store.get(name) or {})}
                if body.title is not None and body.title.strip():
                    md["title"] = body.title.strip()[:YT_TITLE_MAX]
                if body.description is not None and body.description.strip():
                    md["description"] = body.description.strip()[:YT_DESC_MAX]
                if body.tags is not None:
                    tg = body.tags if isinstance(body.tags, list) else str(body.tags).split(",")
                    tg = [t.strip() for t in tg if t and t.strip()]
                    if tg:
                        md["tags"] = tg
                if body.privacy is not None:
                    md["privacy"] = body.privacy
                store[name] = md
                save_publish(pdir, store)
            else:
                raise HTTPException(400, "unknown action")
            done.append({"project": pid, "name": name})
        except HTTPException as e:
            failed.append({"project": pid, "name": name, "reason": str(e.detail)[:200]})
        except Exception as e:  # noqa
            failed.append({"project": pid, "name": name, "reason": str(e)[:200]})
    _ev("publish", f"bulk {body.action}: {len(done)} done" + (f", {len(failed)} not" if failed else ""))
    return {"ok": True, "done": done, "failed": failed}


class StudioStatsIn(BaseModel):
    video_ids: list[str]


@app.post("/api/studio/stats")
def studio_stats(body: StudioStatsIn):
    """YouTube views/likes for published videos (1 quota unit per 50 ids)."""
    import yt_api
    if not yt_api.configured():
        raise HTTPException(409, "no YouTube API key is configured")
    ids = [v for v in body.video_ids if isinstance(v, str) and len(v) == 11][:50]
    try:
        st = yt_api.Client().video_stats(ids)
    except Exception as e:
        raise HTTPException(502, str(e)[:300])
    return {"stats": {k: {"views": v["views"], "likes": v["likes"]} for k, v in st.items()}}


class StudioSettingsIn(BaseModel):
    export_speed: float | None = None
    title_template: str | None = None
    music_credit: str | None = None            # spec 10 C2
    publish: dict | None = None
    schedule: dict | None = None


@app.post("/api/settings")
def settings_save(body: StudioSettingsIn):
    try:
        before, after = _studio.update(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    sch = after.get("schedule") or {}
    _ev("settings", f"settings saved: export {after['export_speed']}x, publish to "
                    f"{', '.join(after['publish']['targets']) or 'nobody'} as {after['publish']['privacy']}; "
                    f"posting schedule {'ON at ' + ', '.join(sch.get('times') or []) if sch.get('enabled') else 'off'}; "
                    f"title format “{after.get('title_template')}”")
    return {"ok": True, "before": before, "settings": after}


class JobResumeIn(BaseModel):
    job_id: str


@app.post("/api/jobs/resume")
def jobs_resume(body: JobResumeIn):
    """Resume a stopped, failed, restart-cut or cap-paused job.

    Ingest: re-queued under the same record, reusing every cached stage.
    Approve/render (finalize): re-run for the open project — clips already
    rendered are kept, so it continues where it stopped."""
    rec = INGEST.get(body.job_id) or _load_ingest(body.job_id)
    if rec is not None:
        if rec.get("status") not in ("cancelled", "error", "budget_paused", "interrupted", "held"):
            raise HTTPException(409, f"job is {rec.get('status')} — nothing to resume")
        if rec.get("status") == "budget_paused":
            spent, cap = _ap_deps()["spend"]()
            if spent >= cap:
                raise HTTPException(409, f"today's spend cap is reached (${spent:.2f} of "
                                         f"${cap:.2f}) — it resumes by itself after midnight ET")
        if _line_count() >= LINE_MAX:
            INGEST[body.job_id] = dict(rec)
            _hold(body.job_id, f"resumed by you — waiting its turn (at most {LINE_MAX} chapters at once); it starts by itself")
            return {"ok": True, "job": body.job_id, "kind": "ingest", "held": True}
        _enqueue_ingest(rec.get("url", ""), False, rec.get("engine", "gemini"),
                        rec.get("variant", ""), rec.get("direct_speech"),
                        job_id=body.job_id, why="resumed by you")
        _autopilot.set_status_for_job(_ingest_mod.PROJECTS, body.job_id, "queued")
        return {"ok": True, "job": body.job_id, "kind": "ingest"}
    j = JOBS.get(body.job_id)
    if j is None:
        try:
            j = json.load(open(os.path.join(_jobs_dir(), f"render_{body.job_id}.json")))
        except (OSError, ValueError):
            j = None
    if j is None:
        raise HTTPException(404, "unknown job")
    if j.get("type") != "finalize":
        raise HTTPException(400, "only ingests and approve-renders can be resumed here")
    if j.get("status") not in ("cancelled", "error"):
        raise HTTPException(409, f"job is {j.get('status')} — nothing to resume")
    if j.get("project") != get_active_project_id():
        raise HTTPException(409, f"open {j.get('project')} first (Projects → Open), then resume")
    r = storyboard_approve(ApproveIn(approved=True))
    return {"ok": True, "job": r.get("job"), "kind": "finalize"}


class ArchiveIn(BaseModel):
    id: str
    action: str          # archive | unarchive | keep


@app.post("/api/projects/archive")
def project_archive_action(body: ArchiveIn):
    import ingest
    pid = body.id
    if not pid or "/" in pid or ".." in pid or pid.startswith("_"):
        raise HTTPException(400, "bad project id")
    pdir = os.path.join(ingest.PROJECTS, pid)
    if not os.path.isdir(pdir):
        raise HTTPException(404, "unknown project")
    if body.action == "archive":
        rec = _archive.archive(pdir, "by you")
    elif body.action == "unarchive":
        rec = _archive.unarchive(pdir)
    elif body.action == "keep":
        try:
            rec = _archive.keep(pdir)
        except ValueError as e:
            raise HTTPException(409, str(e))
    else:
        raise HTTPException(400, "action must be archive, unarchive or keep")
    _autopilot.audit("project_" + body.action, project=pid)
    return {"ok": True, "id": pid, "archive": _archive.view(pdir)}

app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")


