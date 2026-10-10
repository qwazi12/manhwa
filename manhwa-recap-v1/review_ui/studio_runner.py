"""LongForm Studio Runner for Manhwa Recap Studio.

Provides granular stage-by-stage execution, auto-chaining, instant stopping,
and structured database tracking across the 7 manhwa recap stages:
  1. scrape   - Chapter download
  2. split    - YOLO panel slicing
  3. describe - Gemini Vision analysis & Cast Bible
  4. narrate  - Script generation
  5. voice    - Gemini / TTS synthesis
  6. match    - DP narration alignment
  7. render   - Final video composition with Ken Burns & synced captions
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import subprocess
import sys
import threading
import time
from typing import Any

try:
    from . import control, ingest, momentum, usage
except (ImportError, ValueError):
    import control, ingest, momentum, usage
try:
    from .db import SessionLocal
except (ImportError, ValueError):
    from db import SessionLocal
try:
    from .models import QueueItem, StudioProject
except (ImportError, ValueError):
    from models import QueueItem, StudioProject

logger = logging.getLogger("manhwa.studio")

STAGES = ["scrape", "split", "describe", "narrate", "voice", "match", "render"]
STAGE_LABELS = {
    "scrape": "1. Scrape",
    "split": "2. Split Panels",
    "describe": "3. Vision & Bible",
    "narrate": "4. Script",
    "voice": "5. Voice (TTS)",
    "match": "6. DP Alignment",
    "render": "7. Render Video",
}

_lock = threading.Lock()


def get_project_dir(project_id: str) -> pathlib.Path:
    return pathlib.Path(ingest.PROJECTS) / project_id


def list_projects() -> list[dict[str, Any]]:
    with SessionLocal() as s:
        db_projects = s.query(StudioProject).order_by(StudioProject.updated_at.desc()).all()
        out = []
        for p in db_projects:
            p_dir = get_project_dir(p.project_id)
            has_dict = {
                "scrape": (p_dir / "pages").is_dir() and any((p_dir / "pages").iterdir()) if (p_dir / "pages").is_dir() else False,
                "split": (p_dir / "crops").is_dir() and any((p_dir / "crops").iterdir()) if (p_dir / "crops").is_dir() else False,
                "describe": (p_dir / "descriptions.json").is_file(),
                "narrate": (p_dir / "script.json").is_file() or (p_dir / "script.txt").is_file(),
                "voice": (p_dir / "audio").is_dir() and any((p_dir / "audio").glob("beat_*.mp3")) if (p_dir / "audio").is_dir() else False,
                "match": (p_dir / "segments.json").is_file(),
                "render": (p_dir / "exports" / "final.mp4").is_file() or bool(p.render),
            }

            # Cover thumbnail preview
            poster = None
            cover_jpg = p_dir / "cover.jpg"
            if cover_jpg.is_file():
                poster = f"/media/cover.jpg?project={p.project_id}"
            elif (p_dir / "crops").is_dir():
                first_crop = next((p_dir / "crops").glob("*.png"), None)
                if first_crop:
                    poster = f"/panelimg/{first_crop.name}?project={p.project_id}"

            mom = momentum.compute_momentum(momentum.extract_release_date(p, session=s))

            out.append({
                "id": p.id,
                "project_id": p.project_id,
                "series_slug": p.series_slug,
                "chapter_number": p.chapter_number,
                "title": p.title or p.project_id,
                "target_minutes": p.target_minutes,
                "stage": p.stage,
                "stage_status": p.stage_status,
                "stage_message": p.stage_message,
                "poster": poster,
                "momentum": mom,
                "has": has_dict,
                "facts": p.facts,
                "research": p.research,
                "render": p.render,
                "queue_item_id": p.queue_item_id,
                "archive": p.archive,
                "cost_usd": p.cost_usd,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            })
        return out


def get_project(project_id: str) -> dict[str, Any] | None:
    projects = list_projects()
    for p in projects:
        if p["project_id"] == project_id or str(p["id"]) == str(project_id):
            return p
    return None


def run_stage(project_id: str, stage: str, auto: bool = False, until: str = "match") -> str:
    """Run a stage in the background with cooperative cancellation and database state updates."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage '{stage}'. Must be one of {STAGES}")
    if until not in STAGES:
        raise ValueError(f"Unknown target stage '{until}'")

    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir():
        raise FileNotFoundError(f"Project directory not found: {p_dir}")

    def _worker():
        curr_stage = stage
        while True:
            with SessionLocal() as s:
                p = s.query(StudioProject).filter(
                    (StudioProject.project_id == project_id) | (StudioProject.id == project_id)
                ).first()
                if not p:
                    return
                p.stage = curr_stage
                p.stage_status = "running"
                p.stage_message = f"Executing {STAGE_LABELS.get(curr_stage, curr_stage)}..."
                s.commit()

            control.progress(f"Running {curr_stage} on {project_id}")
            control.check()

            try:
                _execute_stage(project_id, curr_stage)
            except control.Cancelled:
                with SessionLocal() as s:
                    p = s.query(StudioProject).filter(
                        (StudioProject.project_id == project_id) | (StudioProject.id == project_id)
                    ).first()
                    if p:
                        p.stage_status = "stopped"
                        p.stage_message = "Stopped by user"
                        s.commit()
                raise
            except Exception as exc:
                logger.error("Stage %s failed on %s: %s", curr_stage, project_id, exc, exc_info=True)
                with SessionLocal() as s:
                    p = s.query(StudioProject).filter(
                        (StudioProject.project_id == project_id) | (StudioProject.id == project_id)
                    ).first()
                    if p:
                        p.stage_status = "error"
                        p.stage_message = str(exc)[:300]
                        s.commit()
                raise

            with SessionLocal() as s:
                p = s.query(StudioProject).filter(
                    (StudioProject.project_id == project_id) | (StudioProject.id == project_id)
                ).first()
                if p:
                    p.stage_status = "done"
                    p.stage_message = f"{STAGE_LABELS.get(curr_stage, curr_stage)} completed successfully"
                    s.commit()

            if not auto or curr_stage == until:
                break

            curr_idx = STAGES.index(curr_stage)
            if curr_idx + 1 >= len(STAGES):
                break
            curr_stage = STAGES[curr_idx + 1]

    job_id = control.start_thread(
        kind="studio",
        label=f"{stage} ({project_id})",
        scope="studio",
        ref=project_id,
        fn=_worker,
    )
    return job_id


def stop_project(project_id: str) -> None:
    control.stop_jobs_for(scope="studio", ref=project_id)
    with SessionLocal() as s:
        p = s.query(StudioProject).filter(
            (StudioProject.project_id == project_id) | (StudioProject.id == project_id)
        ).first()
        if p and p.stage_status in ("running", "queued"):
            p.stage_status = "stopped"
            p.stage_message = "Stopped by user"
            s.commit()


def _execute_stage(project_id: str, stage: str) -> None:
    p_dir = get_project_dir(project_id)
    pj_file = p_dir / "project.json"
    pj_data = json.loads(pj_file.read_text(encoding="utf-8")) if pj_file.is_file() else {}
    url = pj_data.get("url", "")

    control.check()

    if stage == "scrape":
        if not url:
            raise ValueError("No chapter URL found in project.json")
        pages_dir = str(p_dir / "pages")
        os.makedirs(pages_dir, exist_ok=True)
        import scraper
        imgs = scraper.download_chapter(url, pages_dir)
        if not imgs:
            raise RuntimeError("Scraper returned no images")

    elif stage == "split":
        pages_dir = str(p_dir / "pages")
        crops_dir = str(p_dir / "crops")
        os.makedirs(crops_dir, exist_ok=True)
        script_path = str(pathlib.Path(ingest.ROOT) / "panel-split" / "split_panels.py")
        cmd = [sys.executable, script_path, "--batch", pages_dir, "--out", crops_dir]
        res = control.run(cmd, timeout=600)
        if res.returncode != 0:
            raise RuntimeError(f"Panel splitting failed: {res.stderr[:300]}")

    elif stage == "describe":
        crops_dir = str(p_dir / "crops")
        desc_file = str(p_dir / "descriptions.json")
        script_path = str(pathlib.Path(ingest.ROOT) / "panel-describe" / "run.py")
        cmd = [sys.executable, script_path, crops_dir, "--out", desc_file, "--merge"]
        res = control.run(cmd, timeout=1200)
        if res.returncode != 0:
            raise RuntimeError(f"Vision description failed: {res.stderr[:300]}")

    elif stage == "narrate":
        desc_file = p_dir / "descriptions.json"
        if not desc_file.is_file():
            raise FileNotFoundError("descriptions.json not found — run describe first")
        import narrate
        with open(desc_file, "r", encoding="utf-8") as f:
            desc_data = json.load(f)
        bible_path = p_dir / "series_bible.json"
        bible = json.loads(bible_path.read_text(encoding="utf-8")) if bible_path.is_file() else None
        script = narrate.generate_narration(desc_data, bible=bible)
        with open(p_dir / "script.json", "w", encoding="utf-8") as f:
            json.dump(script, f, indent=2)

    elif stage == "voice":
        script_file = p_dir / "script.json"
        if not script_file.is_file():
            raise FileNotFoundError("script.json not found — run narrate first")
        import beat_segmenter, gemini_tts
        with open(script_file, "r", encoding="utf-8") as f:
            script_data = json.load(f)
        beats = beat_segmenter.segment_into_beats(script_data)
        audio_dir = p_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        for i, b in enumerate(beats):
            control.check()
            out_mp3 = str(audio_dir / f"beat_{i:04d}.mp3")
            if not os.path.exists(out_mp3):
                gemini_tts.synth_gemini_tts(b["text"], out_mp3)

    elif stage == "match":
        import matcher
        from segments import build_segments
        desc_file = p_dir / "descriptions.json"
        audio_dir = p_dir / "audio"
        if not desc_file.is_file() or not audio_dir.is_dir():
            raise FileNotFoundError("Missing descriptions or audio directory")
        alignment = matcher.align(str(desc_file), str(audio_dir))
        segs = build_segments(alignment, str(p_dir))
        with open(p_dir / "segments.json", "w", encoding="utf-8") as f:
            json.dump(segs, f, indent=2)

    elif stage == "render":
        # Render safety: build export atomically into .partial, then swap to exports/
        exports_dir = p_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        part_dir = exports_dir / ".partial"
        part_dir.mkdir(parents=True, exist_ok=True)
        out_temp = str(part_dir / "final_render_new.mp4")
        out_live = str(exports_dir / "final.mp4")

        hf_script = str(pathlib.Path(ingest.RECAP) / "hyperframes" / "render_segments.py")
        env = {
            **os.environ,
            "HF_WORKSPACE": str(p_dir),
            "HF_OUTPUT": out_temp,
        }
        res = control.run([sys.executable, hf_script], timeout=3600)
        if res.returncode != 0:
            raise RuntimeError(f"HyperFrames video render failed: {res.stderr[:300]}")

        # Atomic Swap: replace only once 100% finished
        if os.path.exists(out_temp):
            if os.path.exists(out_live):
                os.remove(out_live)
            os.replace(out_temp, out_live)

            # Record render info in SQLite
            with SessionLocal() as s:
                p = s.query(StudioProject).filter(
                    (StudioProject.project_id == project_id) | (StudioProject.id == project_id)
                ).first()
                if p:
                    p.render = {
                        "file": out_live,
                        "size": os.stat(out_live).st_size,
                        "completed_at": time.time(),
                    }
                    s.commit()
