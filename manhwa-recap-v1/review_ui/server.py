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
            "beats": [{"index": b["index"], "text": b["text"]} for b in s.get("beats", [])],
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


def _write_segments(segs):
    tmp = SEGMENTS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(segs, f, indent=2)
    os.replace(tmp, SEGMENTS_JSON)


def _rerender(seg):
    """Re-render one segment's clip from the (edited) seg dict."""
    import render_segments as rs
    rs.PANEL_DIR = _panel_dir()
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
    tp = os.path.join(THUMBS, f"panel_{panel_id}.jpg")
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
    try:
        _rerender(seg)
    except Exception as e:
        raise HTTPException(500, f"re-render failed: {e}")
    return {"ok": True, "seg_index": seg_index, "panel_id": body.panel_id}


# --- narration edit → re-TTS (REST; the google-cloud SDK import hangs) -----
GAP_SEC = 0.35  # inter-beat gap, matches tts.GAP_SEC


def _tts_key():
    for line in open(os.path.join(RECAP, "..", ".env")):
        if line.startswith("TTS_API_KEY"):
            return line.split("=", 1)[1].strip()
    return None


def _synth_rest(text, out_path):
    """Synthesize one beat to MP3 via the Chirp REST endpoint + API key,
    using certifi's CA bundle (avoids the hanging google-cloud SDK import)."""
    import base64, ssl, urllib.request
    key = _tts_key()
    if not key:
        raise RuntimeError("TTS_API_KEY not found in .env")
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
    with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
        audio = base64.b64decode(json.load(r)["audioContent"])
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
    try:
        _rerender(seg)
    except Exception as e:
        raise HTTPException(500, f"re-render failed: {e}")
    return {"ok": True, "seg_index": seg_index, "dur": seg["dur"]}


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
    for s in (seg, tail):             # both halves are new content -> re-render
        _rerender(s)
    _write_segments(segs)
    return {"ok": True, "new_seg_index": tail["seg_index"]}


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
    _rerender(a)
    _write_segments(segs)
    return {"ok": True, "seg_index": a["seg_index"]}


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
app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
