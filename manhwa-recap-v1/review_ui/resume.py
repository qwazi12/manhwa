"""Background job persistence, heartbeats, and startup recovery.

Ensures server restarts cleanly flag or auto-resume interrupted jobs,
preventing zombie jobs or broken project states on redeploys.
"""

from __future__ import annotations

import datetime
import logging
import threading
import uuid
from typing import Any, Callable

try:
    from . import control
except (ImportError, ValueError):
    import control
try:
    from .db import SessionLocal
except (ImportError, ValueError):
    from db import SessionLocal
try:
    from .models import ResumableJob, StudioProject
except (ImportError, ValueError):
    from models import ResumableJob, StudioProject

logger = logging.getLogger("manhwa.resume")

MAX_RESUMES = 2
BOOT = uuid.uuid4().hex[:16]
HEARTBEAT_SEC, STALE_SEC = 20, 75
_launchers: dict[str, Callable[[dict, dict, int], Any]] = {}
_lock = threading.Lock()


def launcher(kind: str):
    """Register a restarter callback for kind: fn(params, checkpoint, rid)."""
    def deco(fn):
        _launchers[kind] = fn
        return fn
    return deco


def begin(kind: str, label: str, params: dict) -> int:
    """Register a new long-running job in SQLite."""
    with SessionLocal() as s:
        row = ResumableJob(
            kind=kind,
            label=label,
            params=params,
            checkpoint={},
            status="running",
            owner=BOOT,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id


def finish(rid: int, status: str = "done", checkpoint: dict | None = None, last_error: str | None = None) -> None:
    """Mark job as finished (done, stopped, failed, or budget_paused)."""
    with SessionLocal() as s:
        row = s.get(ResumableJob, rid)
        if row:
            row.status = status
            if checkpoint is not None:
                row.checkpoint = checkpoint
            if last_error:
                row.last_error = str(last_error)[:500]
            s.commit()


def checkpoint(rid: int, cp: dict) -> None:
    """Update progress checkpoint and heartbeat."""
    with SessionLocal() as s:
        row = s.get(ResumableJob, rid)
        if row and row.status == "running":
            row.checkpoint = cp
            s.commit()


def recover_interrupted() -> dict[str, int]:
    """Startup recovery: mark stale or interrupted jobs and studio stages as stopped."""
    recovered_stages = 0
    recovered_jobs = 0

    with SessionLocal() as s:
        # 1. Recover ResumableJob rows whose owner is not this process
        stale_jobs = s.query(ResumableJob).filter(
            ResumableJob.status == "running",
            ResumableJob.owner != BOOT,
        ).all()

        for j in stale_jobs:
            if j.attempts < MAX_RESUMES and j.kind in _launchers:
                j.attempts += 1
                j.owner = BOOT
                logger.info("Auto-resuming interrupted job #%d (%s, attempt %d/%d)", j.id, j.label, j.attempts, MAX_RESUMES)
                try:
                    fn = _launchers[j.kind]
                    threading.Thread(target=fn, args=(j.params, j.checkpoint, j.id), daemon=True).start()
                    recovered_jobs += 1
                except Exception as exc:
                    j.status = "failed"
                    j.last_error = f"Auto-resume failed: {exc}"
            else:
                j.status = "stopped"
                j.last_error = "Interrupted by server restart"
                recovered_jobs += 1

        # 2. Recover StudioProject rows that were left running
        stuck_projects = s.query(StudioProject).filter(
            StudioProject.stage_status == "running"
        ).all()

        for p in stuck_projects:
            p.stage_status = "stopped"
            p.stage_message = f"{p.stage} was interrupted by a server restart — re-run it"
            logger.warning("Studio Project %s: %s was interrupted by server restart", p.project_id, p.stage)
            recovered_stages += 1

        s.commit()

    return {"jobs": recovered_jobs, "stages": recovered_stages}
