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
import subprocess
import sys
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
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



# ----------------------------------------------------------------- project-scoped workspace
def get_active_project_id():
    path = os.path.join(WORK, "active_project.txt")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


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


# ----------------------------------------------------------------- state
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
def thumb_path(seg_index):
    t_dir = os.path.join(active_project_dir(), "thumbnails")
    os.makedirs(t_dir, exist_ok=True)
    return os.path.join(t_dir, f"seg_{seg_index:03d}.jpg")


def ensure_thumb(seg):
    """Make a small JPEG thumbnail from the segment's panel image (cached)."""
    out = thumb_path(seg["seg_index"])
    if os.path.exists(out):
        return out
    src = seg.get("panel_file")
    if not src or not os.path.exists(src):
        return None
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vf",
             "scale=200:-1", "-frames:v", "1", out],
            check=True, capture_output=True)
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
    for s in segs:
        st = review.get(str(s["seg_index"]), {}).get("status", "pending")
        note = review.get(str(s["seg_index"]), {}).get("note", "")
        clip_ok = os.path.exists(os.path.join(active_project_dir(), s.get("clip", "")))
        if st == "approved":
            approved_dur += s.get("dur", 0)
        ensure_thumb(s)
        out.append({
            "seg_index": s["seg_index"],
            "panel_id": s["panel_id"],
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
def clip(seg_index: int):
    segs = load_segments()
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    if not seg:
        raise HTTPException(404, "segment not found")
    path = os.path.join(active_project_dir(), seg.get("clip", ""))
    if not os.path.exists(path):
        raise HTTPException(404, "clip not rendered yet")
    # FileResponse handles HTTP Range requests -> <video> seeking works
    return FileResponse(path, media_type="video/mp4")


@app.get("/thumb/{seg_index}")
def thumb(seg_index: int):
    segs = load_segments()
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    if not seg:
        raise HTTPException(404, "segment not found")
    p = ensure_thumb(seg)
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
    return {"ok": True, "seg_index": seg_index, "status": body.status}


class ExportIn(BaseModel):
    speed: float = 1.0     # 1.0 = normal, 1.5 = fast pass


@app.post("/api/export")
def export(body: ExportIn):
    """Concat ONLY approved clips (in order), optionally through speed_up."""
    segs = load_segments()
    review = load_review()
    approved = [s for s in segs
                if review.get(str(s["seg_index"]), {}).get("status") == "approved"]
    approved = [s for s in approved
                if os.path.exists(os.path.join(active_project_dir(), s.get("clip", "")))]
    if not approved:
        raise HTTPException(400, "no approved clips with rendered video")
    listfile = os.path.join(active_exports_dir(), "concat.txt")
    with open(listfile, "w") as f:
        for s in approved:
            f.write(f"file '{os.path.join(active_project_dir(), s['clip'])}'\n")
    out = os.path.join(active_exports_dir(), "review_export.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
         "-c", "copy", out], check=True, capture_output=True)
    final = out
    if abs(body.speed - 1.0) > 1e-3:
        sped = os.path.join(active_exports_dir(), f"review_export_{body.speed}x.mp4")
        subprocess.run(
            [sys.executable, os.path.join(RECAP, "speed_up.py"),
             out, sped, str(body.speed)], check=True, capture_output=True)
        final = sped
    return {"ok": True, "clips": len(approved), "output": os.path.basename(final),
            "url": f"/export/{os.path.basename(final)}"}


@app.get("/export/{name}")
def get_export(name: str):
    path = os.path.join(active_exports_dir(), os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404, "export not found")
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/render-missing")
def render_missing():
    """Render any segment that lacks a clip, via render_segments.py --only."""
    segs = load_segments()
    pdir = active_project_dir()
    missing = [s["seg_index"] for s in segs
               if not os.path.exists(os.path.join(pdir, s.get("clip", "")))]
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
def _panel_dir():
    segs = load_segments()
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


def _rerender(seg):
    """Re-render one segment's clip from the (edited) seg dict."""
    import render_segments as rs
    rs.PANEL_DIR = _panel_dir()
    rs.WORK = active_project_dir()
    rs.CLIPS = os.path.join(rs.WORK, "clips")
    rs.ASSETS = os.path.join(rs.WORK, "assets")
    rs.ensure_project()
    rs.render_segment(seg, AUDIO_DIR)
    # thumbnail may be stale after a panel swap
    tp = thumb_path(seg["seg_index"])
    if os.path.exists(tp):
        os.remove(tp)


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
def panelimg(panel_id: str, thumb: int = 0):
    path = os.path.join(_panel_dir(), f"{panel_id}.png")
    if not os.path.exists(path):
        raise HTTPException(404, "panel not found")
    if not thumb:
        return FileResponse(path, media_type="image/png")
    t_dir = os.path.join(active_project_dir(), "thumbnails")
    os.makedirs(t_dir, exist_ok=True)
    tp = os.path.join(t_dir, f"panel_{panel_id}.jpg")
    if not os.path.exists(tp):
        try:
            subprocess.run(["ffmpeg", "-y", "-i", path, "-vf", "scale=160:-1",
                            "-frames:v", "1", tp], check=True, capture_output=True)
        except Exception:
            raise HTTPException(404, "thumb failed")
    return FileResponse(tp, media_type="image/jpeg")


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


def _synth_rest(text, out_path):
    """Synthesize one beat to MP3 via the Chirp REST endpoint + API key,
    using certifi's CA bundle (avoids the hanging google-cloud SDK import)."""
    import base64, ssl, urllib.request
    key = _tts_key()
    if not key:
        raise RuntimeError("TTS_API_KEY not set (checked env var and local .env)")
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    body = {"input": {"text": text},
            "voice": {"languageCode": "en-US", "name": "en-US-Chirp3-HD-Charon"},
            "audioConfig": {"audioEncoding": "MP3"}}
    req = urllib.request.Request(
        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})

    def _call():
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            return base64.b64decode(json.load(r)["audioContent"])

    with usage.gate("tts", len(text), model="chirp3-hd-charon"):
        audio = _call()
    with open(out_path, "wb") as f:
        f.write(audio)


def _recompute_timeline(segs):
    """Rebuild every beat/segment start-end from the CURRENT beat-audio
    durations (sequential with GAP_SEC between beats), so timing stays exact
    after a re-TTS changes a clip's length. Only the edited clip re-renders;
    downstream clips are untouched (concat is sequential), but their manifest
    times are kept accurate for display."""
    import render_segments as rs
    t = 0.0
    for seg in segs:
        for b in seg["beats"]:
            a = os.path.join(AUDIO_DIR, f"beat_{b['index']:03d}.mp3")
            dur = rs.ffprobe_dur(a) if os.path.exists(a) else (b["end"] - b["start"])
            b["start"] = round(t, 3)
            b["end"] = round(t + dur, 3)
            t += dur + GAP_SEC
        seg["start"] = seg["beats"][0]["start"]
        seg["end"] = seg["beats"][-1]["end"]
        seg["dur"] = round(seg["end"] - seg["start"], 3)


class NarrationIn(BaseModel):
    beats: list   # [{"index": int, "text": str}, ...]


@app.post("/api/segments/{seg_index}/narration")
def edit_narration(seg_index: int, body: NarrationIn):
    segs = load_segments()
    seg = next((s for s in segs if s["seg_index"] == seg_index), None)
    if not seg:
        raise HTTPException(404, "segment not found")
    _snapshot()
    edited = {e["index"]: e["text"].strip() for e in body.beats}
    for b in seg["beats"]:
        if b["index"] in edited and edited[b["index"]] and edited[b["index"]] != b["text"]:
            b["text"] = edited[b["index"]]
            try:
                _synth_rest(b["text"],
                            os.path.join(AUDIO_DIR, f"beat_{b['index']:03d}.mp3"))
            except Exception as e:
                raise HTTPException(500, f"TTS failed: {e}")
    _recompute_timeline(segs)          # new audio length shifts timing
    _write_segments(segs)
    job = _start_render([seg_index])   # clip re-renders in background; preview is instant
    return {"ok": True, "seg_index": seg_index, "dur": seg["dur"], "job": job}


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


def _run_render_job(job_id, seg_indices):
    JOBS[job_id]["status"] = "running"
    try:
        for si in seg_indices:
            segs = load_segments()
            seg = next((s for s in segs if s["seg_index"] == si), None)
            if seg:
                _rerender(seg)
                _write_segments(segs)
            JOBS[job_id]["done"] += 1
        JOBS[job_id]["status"] = "done"
    except Exception as e:  # noqa
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)


def _start_render(seg_indices):
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "done": 0, "total": len(seg_indices),
                    "seg_indices": seg_indices, "error": None}
    threading.Thread(target=_run_render_job, args=(job_id, seg_indices),
                     daemon=True).start()
    return job_id


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


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
        with open(os.path.join(_JOBS_DIR, f"{job_id}.json"), "w") as f:
            json.dump(INGEST[job_id], f)
    except Exception:
        pass


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


def _active_ingest_for_url(url):
    """Return the job_id of a running/queued job for this URL's project, if any
    — so submitting the same chapter twice reuses the one job (no double spend,
    no folder race)."""
    slug = _ingest_mod._slug(url)
    for jid, j in INGEST.items():
        if j.get("status") in ("queued", "running") and \
                _ingest_mod._slug(j.get("url", "")) == slug:
            return jid
    return None


def _run_ingest_job(job_id, url):
    import ingest
    INGEST[job_id]["status"] = "running"
    _persist_ingest(job_id)

    def progress(stage, msg, pct):
        INGEST[job_id].update(stage=stage, msg=msg, pct=pct)
        _persist_ingest(job_id)

    try:
        meta = ingest.run_ingest(url, progress, job_id=job_id)
        INGEST[job_id].update(status="done", project=meta, pct=100)
    except subprocess.CalledProcessError as e:
        INGEST[job_id].update(status="error",
                              error=(e.stderr or str(e))[-400:])
    except usage.UsageCapExceeded as e:
        INGEST[job_id].update(status="error", error=f"USAGE CAP EXCEEDED: {e}")
    except Exception as e:  # noqa
        INGEST[job_id].update(status="error", error=str(e))
    INGEST[job_id]["ended"] = time.time()
    _persist_ingest(job_id)


class IngestIn(BaseModel):
    url: str


@app.post("/api/ingest")
def start_ingest(body: IngestIn):
    url = body.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "please paste a full chapter URL (http/https)")
    # DEDUPE: if this chapter is already ingesting, return that job — do not
    # start a second one (avoids a folder race + doubled Gemini/TTS spend).
    existing = _active_ingest_for_url(url)
    if existing:
        return {"job": existing, "stages": ingest_stages(), "existing": True}
    job_id = uuid.uuid4().hex[:12]
    INGEST[job_id] = {"stage": "queued", "pct": 0, "msg": "queued",
                      "status": "queued", "error": None, "project": None,
                      "url": url, "ts": time.time()}
    _persist_ingest(job_id)
    threading.Thread(target=_run_ingest_job, args=(job_id, url),
                     daemon=True).start()
    return {"job": job_id, "stages": ingest_stages(), "existing": False}


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


@app.get("/api/logs/ingest")
def logs_ingest():
    """Every ingest job (durable across restarts), newest first."""
    return {"jobs": _all_ingest_jobs()}


@app.get("/api/projects")
def projects_list():
    import ingest
    items = [{"id": "chapter-2 (current)", "url": "loaded", "active": True,
              "n_segments": len(load_segments())}]
    finished_slugs = set()
    for m in ingest.list_projects():
        items.append({**m, "active": False})
        finished_slugs.add(m.get("id"))
    # IN-PROGRESS ingests (running/queued) — clickable to watch live status.
    in_progress = []
    for jid, j in INGEST.items():
        if j.get("status") in ("queued", "running"):
            in_progress.append({
                "job": jid, "url": j.get("url"),
                "slug": ingest._slug(j.get("url", "")),
                "stage": j.get("stage"), "pct": j.get("pct", 0),
                "status": j.get("status"),
            })
    return {"projects": items, "in_progress": in_progress}


class ActivateIn(BaseModel):
    id: str


@app.post("/api/activate")
def activate_project(body: ActivateIn):
    """Point the studio at an ingested project: load its segments + audio."""
    global AUDIO_DIR, DESCRIPTIONS
    import ingest
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
    if est_cost_usd >= usage.MAX_DAILY_SPEND_USD * 0.9:
        warnings.append(f"Daily spend is near cap (${est_cost_usd:.2f}/${usage.MAX_DAILY_SPEND_USD:.2f}).")
        
    status = "OK"
    if len(warnings) > 0:
        status = "WARNING"
    if pct > 95.0 or est_cost_usd >= usage.MAX_DAILY_SPEND_USD:
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
            "spend_limit_usd": usage.MAX_DAILY_SPEND_USD,
            "warnings": warnings
        },
        "config": {
            "gemini_api_key_configured": bool(os.environ.get("GEMINI_API_KEY")),
            "tts_api_key_configured": bool(os.environ.get("TTS_API_KEY") or os.environ.get("GEMINI_API_KEY"))
        }
    }


@app.get("/api/debug/test-planner")
def debug_test_planner():
    pdir = active_project_dir()
    segments_path = os.path.join(pdir, "segments.json")
    if not os.path.exists(segments_path):
        return {"error": "no segments found"}
    import sys
    sys.path.insert(0, os.path.join(RECAP, "hyperframes"))
    import shot_planner
    with open(segments_path) as f:
        segs = json.load(f)
    shots = []
    for seg in segs:
        for b in seg["beats"]:
            shots.append({
                "index": b["index"],
                "beat_text": b["text"],
                "panel_id": seg["panel_id"],
                "panel_file": os.path.join(pdir, "crops", f"{seg['panel_id']}.png"),
                "width": 1000,
                "height": 1000
            })
    planned = shot_planner.plan_shots(shots[:5], os.path.join(pdir, "descriptions.json"), os.path.join(pdir, "crops"))
    return {"planned": planned}


@app.get("/api/debug/ps")
def debug_ps():
    import subprocess
    try:
        out = subprocess.check_output(["ps", "aux"], text=True)
        return {"ok": True, "ps": out}
    except Exception as e:
        return {"ok": False, "error": str(e)}


app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")


