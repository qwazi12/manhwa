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
import subprocess
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
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


# ----------------------------------------------------------------- state
def load_segments():
    if not os.path.exists(SEGMENTS_JSON):
        return []
    with open(SEGMENTS_JSON, encoding="utf-8") as f:
        return json.load(f)


def load_review():
    try:
        with open(REVIEW_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_review(state):
    tmp = REVIEW_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, REVIEW_JSON)


# ------------------------------------------------------------- thumbnails
def thumb_path(seg_index):
    return os.path.join(THUMBS, f"seg_{seg_index:03d}.jpg")


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
        clip_ok = os.path.exists(os.path.join(WORK, s.get("clip", "")))
        if st == "approved":
            approved_dur += s.get("dur", 0)
        ensure_thumb(s)
        out.append({
            "seg_index": s["seg_index"],
            "panel_id": s["panel_id"],
            "start": s["start"], "end": s["end"], "dur": s.get("dur"),
            "text": " ".join(b["text"] for b in s.get("beats", [])),
            "n_beats": len(s.get("beats", [])),
            "status": st, "note": note,
            "has_clip": clip_ok,
            "clip_url": f"/clip/{s['seg_index']}" if clip_ok else None,
            "thumb_url": f"/thumb/{s['seg_index']}",
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
    path = os.path.join(WORK, seg.get("clip", ""))
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
                if os.path.exists(os.path.join(WORK, s.get("clip", "")))]
    if not approved:
        raise HTTPException(400, "no approved clips with rendered video")
    listfile = os.path.join(EXPORTS, "concat.txt")
    with open(listfile, "w") as f:
        for s in approved:
            f.write(f"file '{os.path.join(WORK, s['clip'])}'\n")
    out = os.path.join(EXPORTS, "review_export.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
         "-c", "copy", out], check=True, capture_output=True)
    final = out
    if abs(body.speed - 1.0) > 1e-3:
        sped = os.path.join(EXPORTS, f"review_export_{body.speed}x.mp4")
        subprocess.run(
            [sys.executable, os.path.join(RECAP, "speed_up.py"),
             out, sped, str(body.speed)], check=True, capture_output=True)
        final = sped
    return {"ok": True, "clips": len(approved), "output": os.path.basename(final),
            "url": f"/export/{os.path.basename(final)}"}


@app.get("/export/{name}")
def get_export(name: str):
    path = os.path.join(EXPORTS, os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404, "export not found")
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/render-missing")
def render_missing():
    """Render any segment that lacks a clip, via render_segments.py --only."""
    segs = load_segments()
    missing = [s["seg_index"] for s in segs
               if not os.path.exists(os.path.join(WORK, s.get("clip", "")))]
    done = []
    for i in missing:
        r = subprocess.run(
            [sys.executable, os.path.join(HF, "render_segments.py"),
             "--only", str(i)],
            capture_output=True, text=True)
        if r.returncode == 0:
            done.append(i)
    return {"ok": True, "rendered": done, "still_missing": len(missing) - len(done)}


# static SPA last so /api and /clip take precedence
app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
