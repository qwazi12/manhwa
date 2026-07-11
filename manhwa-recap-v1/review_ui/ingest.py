"""
Chapter-URL ingestion pipeline for the review UI.

Given a chapter URL, run the whole pipeline into a self-contained project dir
and produce a segments.json the review UI can open:

  scrape  → download the chapter's page images         (scraper.download_chapter)
  split   → cut pages into panels, vision-segment tall  (split_panels.py --batch)
  describe→ OCR + visual description per panel           (panel-describe/run.py)
  narrate → write narration FROM the panels              (narrate.generate_narration)
  voice   → segment into beats + TTS each beat           (beat_segmenter + REST TTS)
  match   → align beats → panels (DP aligner)            (matcher)
  segment → group into render segments                   (segments.build_segments)

Every stage reports progress through a callback so the UI can show a live
status. Clips are NOT rendered here — that stays on-demand ("Render missing" /
export), so ingestion finishes fast and the user reviews before paying render
time. All stages run under the recap venv (this interpreter).
"""

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(RECAP, ".."))
PROJECTS = os.path.join(HERE, "projects")
PY = sys.executable  # recap venv python (has google-genai, numpy, PIL, whisper)

STAGES = ["scrape", "split", "describe", "narrate", "voice", "match", "segment"]


def parse_series_chapter(url):
    import hashlib
    url_clean = url.strip().rstrip("/").lower()
    m1 = re.search(r"/comics/([^/]+)/chapter/([^/]+)", url_clean)
    if m1:
        return m1.group(1), m1.group(2)
    m2 = re.search(r"/comics/([^/]+)/chapters?/([^/]+)", url_clean)
    if m2:
        return m2.group(1), m2.group(2)
    m3 = re.search(r"/([^/]+)/chapter/([^/]+)", url_clean)
    if m3:
        return m3.group(1), m3.group(2)
    parts = [p for p in url_clean.split("/") if p]
    if len(parts) >= 2:
        if "." in parts[-2] or len(parts[-2]) < 2:
            h = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
            return "series", h
        return parts[-2], parts[-1]
    h = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return "series", h


def clean_series_slug(slug):
    return re.sub(r"-[a-f0-9]{8}$", "", slug)


def to_title_case(slug):
    return slug.replace("-", " ").title()


def _slug(url):
    series, chapter = parse_series_chapter(url)
    series_slug = re.sub(r"[^a-z0-9_-]+", "-", clean_series_slug(series))
    chapter_slug = re.sub(r"[^a-z0-9_-]+", "-", chapter)
    return f"{series_slug}_{chapter_slug}"


def run_ingest(url, progress, tts_key=None, job_id=None):
    """Run the pipeline for one chapter URL. `progress(stage, msg, pct)` is
    called as it advances. Returns the finished project dict.

    `job_id` scopes the cost/abuse guardrails (usage.py): it's set for this
    thread (in-process narrate/matcher/TTS calls) and passed via RECAP_JOB_ID
    env var to the describe subprocess, so every external API call this
    ingestion makes is attributed to the same job for the per-job cap."""
    sys.path.insert(0, RECAP)
    sys.path.insert(0, os.path.join(RECAP, "hyperframes"))
    import scraper, narrate, beat_segmenter, matcher
    from segments import build_segments
    import usage
    job_id = job_id or "unknown"
    usage.set_job(job_id)

    proj_id = _slug(url)
    proj = os.path.join(PROJECTS, proj_id)
    pages = os.path.join(proj, "pages")
    crops = os.path.join(proj, "crops")
    audio = os.path.join(proj, "audio")
    for d in (pages, crops, audio):
        os.makedirs(d, exist_ok=True)
    desc_path = os.path.join(proj, "descriptions.json")

    # 1. scrape -----------------------------------------------------------
    progress("scrape", "Downloading chapter images…", 5)
    imgs = scraper.download_chapter(url, pages)
    if not imgs:
        raise RuntimeError("scraper downloaded no images (blocked or bad URL)")
    progress("scrape", f"Downloaded {len(imgs)} pages.", 12)

    # 2. split (with vision segmentation of tall panels) ------------------
    progress("split", "Splitting pages into panels…", 18)
    subp_env = {**os.environ, "RECAP_JOB_ID": job_id}
    split_p = subprocess.run(
        [PY, os.path.join(ROOT, "panel-split", "split_panels.py"),
         "--input", pages, "--out", crops, "--batch"],
        cwd=os.path.join(ROOT, "panel-split"),
        env=subp_env, capture_output=True, text=True)
    if split_p.returncode != 0:
        if "USAGE CAP EXCEEDED" in (split_p.stderr or ""):
            raise usage.UsageCapExceeded(split_p.stderr.strip().splitlines()[-1])
        raise subprocess.CalledProcessError(split_p.returncode, split_p.args,
                                            split_p.stdout, split_p.stderr)
    n_crops = len([f for f in os.listdir(crops) if f.lower().endswith(".png")])
    progress("split", f"{n_crops} panel crops.", 30)

    # 3. describe ---------------------------------------------------------
    progress("describe", "Describing panels (Gemini vision)…", 35)
    desc_p = subprocess.run(
        [PY, os.path.join(ROOT, "panel-describe", "run.py"),
         "--input", crops, "--out", desc_path, "--model", "gemini-3.5-flash"],
        cwd=os.path.join(ROOT, "panel-describe"),
        env=subp_env, capture_output=True, text=True)
    if desc_p.returncode != 0:
        if "USAGE CAP EXCEEDED" in (desc_p.stderr or ""):
            raise usage.UsageCapExceeded(desc_p.stderr.strip().splitlines()[-1])
        raise subprocess.CalledProcessError(desc_p.returncode, desc_p.args,
                                            desc_p.stdout, desc_p.stderr)
    progress("describe", "Descriptions ready.", 55)

    # 4. narrate (write narration FROM the panels) -----------------------
    progress("narrate", "Writing narration from panels…", 60)
    panels = narrate.load_panels(desc_path)
    script, _ = narrate.generate_narration(panels, verbose=False)
    open(os.path.join(proj, "script.txt"), "w").write(script)
    beats = beat_segmenter.segment_beats(script)
    progress("narrate", f"{len(beats)} narration beats.", 70)

    # 5. voice (TTS each beat via REST; recompute timeline) --------------
    progress("voice", f"Synthesizing {len(beats)} beats (TTS)…", 72)
    import server as srv  # reuse the REST TTS helper (certifi CA, no SDK)
    t = 0.0
    for i, b in enumerate(beats):
        out = os.path.join(audio, f"beat_{b['index']:03d}.mp3")
        if not os.path.exists(out):
            srv._synth_rest(b["text"], out)
        d = _dur(out)
        b["start"], b["end"] = round(t, 3), round(t + d, 3)
        t += d + 0.35
        if i % 10 == 0:
            progress("voice", f"beat {i+1}/{len(beats)} · {t:.0f}s", 72 + int(15 * i / len(beats)))
    progress("voice", f"Narration timeline {t:.0f}s.", 88)

    # 6. match (DP aligner) ----------------------------------------------
    progress("match", "Matching beats to panels…", 90)
    pj = json.load(open(desc_path))
    for p in pj:
        if p.get("file") and not os.path.isabs(p["file"]):
            p["file"] = os.path.join(crops, p["file"])
    pj = [p for p in pj if p.get("ok", True) and p.get("width") and p.get("height")]
    assigns, method = matcher.match_beats_to_panels(beats, pj)
    shots = matcher.build_timeline(beats, pj, assigns)
    progress("match", f"{len({s['panel_id'] for s in shots})} distinct panels.", 95)

    # 7. segment ----------------------------------------------------------
    progress("segment", "Building render segments…", 97)
    segs = build_segments(shots)
    for s in segs:
        s["clip"] = f"clips/seg_{s['seg_index']:03d}.mp4"
    json.dump(segs, open(os.path.join(proj, "segments.json"), "w"), indent=2)
    os.makedirs(os.path.join(proj, "clips"), exist_ok=True)
    series, chapter = parse_series_chapter(url)
    series_title = to_title_case(clean_series_slug(series))
    chapter_title = to_title_case(chapter)
    meta = {"id": proj_id, "url": url, "crops": crops, "audio": audio,
            "descriptions": desc_path, "n_segments": len(segs),
            "duration": round(shots[-1]["end"], 1) if shots else 0,
            "series": series_title, "chapter": chapter_title}
    json.dump(meta, open(os.path.join(proj, "project.json"), "w"), indent=2)
    progress("segment", f"Done — {len(segs)} segments, {meta['duration']}s.", 100)
    return meta


def _dur(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], text=True)
    return float(out.strip())


def list_projects():
    if not os.path.isdir(PROJECTS):
        return []
    out = []
    for pid in sorted(os.listdir(PROJECTS)):
        pj = os.path.join(PROJECTS, pid, "project.json")
        if os.path.exists(pj):
            try:
                data = json.load(open(pj))
                if "series" not in data or "chapter" not in data:
                    parts = pid.split("_")
                    data["series"] = data.get("series", to_title_case(clean_series_slug(parts[0])))
                    data["chapter"] = data.get("chapter", to_title_case(parts[1]) if len(parts) > 1 else pid)
                out.append(data)
            except Exception:
                pass
    return out
