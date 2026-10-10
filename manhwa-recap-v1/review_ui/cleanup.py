"""Automated disk hygiene, post-publication sweeps, and LRU cache eviction."""

from __future__ import annotations

import datetime
import logging
import os
import pathlib
import shutil
import time

from sqlalchemy.orm import Session

try:
    from .ops_config import settings
except (ImportError, ValueError):
    from ops_config import settings
try:
    from .models import QueueItem, StudioProject
except (ImportError, ValueError):
    from models import QueueItem, StudioProject

logger = logging.getLogger("manhwa.cleanup")


def cleanup_render_intermediates(out_dir: pathlib.Path) -> dict:
    """Remove intermediate raw audio and segment files after composition finishes."""
    if not out_dir.exists():
        return {"files_removed": 0, "bytes_freed": 0}
    patterns = [
        "*.mov",
        "*.wav",
        "segments*.txt",
        "concat_list*.txt",
        "*.tmp",
    ]
    removed = 0
    freed_bytes = 0
    for pat in patterns:
        for f in out_dir.glob(pat):
            try:
                sz = f.stat().st_size
                f.unlink(missing_ok=True)
                removed += 1
                freed_bytes += sz
            except Exception as exc:
                logger.warning("Intermediate cleanup failed for %s: %s", f, exc)

    return {"files_removed": removed, "bytes_freed": freed_bytes}


def prune_lru_cache(cache_dir: pathlib.Path, max_bytes: int, target_ratio: float = 0.8) -> dict:
    """Evict oldest entries in cache_dir until size drops below target_ratio * max_bytes."""
    if not cache_dir.exists() or not cache_dir.is_dir():
        return {"files_removed": 0, "bytes_freed": 0, "remaining_bytes": 0}
    entries: list[tuple[pathlib.Path, int, float]] = []
    total_bytes = 0
    for p in cache_dir.iterdir():
        if p.is_file():
            try:
                st = p.stat()
                entries.append((p, st.st_size, st.st_mtime))
                total_bytes += st.st_size
            except OSError:
                continue

    if total_bytes <= max_bytes:
        return {"files_removed": 0, "bytes_freed": 0, "remaining_bytes": total_bytes}

    entries.sort(key=lambda x: x[2])
    target_bytes = int(max_bytes * target_ratio)
    freed = 0
    removed = 0
    for path, sz, _ in entries:
        try:
            path.unlink(missing_ok=True)
            freed += sz
            total_bytes -= sz
            removed += 1
        except OSError:
            pass
        if total_bytes <= target_bytes:
            break

    return {"files_removed": removed, "bytes_freed": freed, "remaining_bytes": total_bytes}


def cleanup_leftover_renders(max_age_seconds: int = 3600) -> dict:
    """Delete abandoned render_new/ directories older than max_age_seconds."""
    from . import ingest
    projects_dir = pathlib.Path(ingest.PROJECTS)
    if not projects_dir.exists():
        return {"folders_removed": 0}
    now = time.time()
    removed = 0
    for p in projects_dir.iterdir():
        if p.is_dir():
            rn = p / "render_new"
            if rn.exists():
                try:
                    if now - rn.stat().st_mtime > max_age_seconds:
                        shutil.rmtree(rn, ignore_errors=True)
                        removed += 1
                        logger.info("Cleaned abandoned render directory: %s", rn)
                except Exception as exc:
                    logger.warning("Failed to clean %s: %s", rn, exc)
    return {"folders_removed": removed}


def purge_expired(s: Session, retention_days: int | None = None) -> dict:
    """Automatic 4-day post-publication hygiene sweep. Clears heavy temporary slices for published chapters."""
    days = retention_days or settings.cleanup_retention_days
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    from . import ingest
    projects_dir = pathlib.Path(ingest.PROJECTS)

    published = s.query(QueueItem).filter(
        QueueItem.status == "posted",
        QueueItem.published_at.is_not(None),
        QueueItem.published_at < cutoff,
    ).all()

    cleaned = 0
    freed_bytes = 0
    for q in published:
        if not q.project_id:
            continue
        p_dir = projects_dir / q.project_id
        if not p_dir.is_dir():
            continue
        # Clean intermediate raw crop slices and cache
        crops_dir = p_dir / "crops"
        if crops_dir.exists():
            for f in crops_dir.glob("*.png"):
                try:
                    sz = f.stat().st_size
                    f.unlink(missing_ok=True)
                    freed_bytes += sz
                except Exception:
                    pass
            cleaned += 1

    return {"projects_swept": cleaned, "freed_mb": round(freed_bytes / (1024 * 1024), 2)}


def startup_cleanup() -> dict:
    leftovers = cleanup_leftover_renders()
    return {"leftover_renders": leftovers}


def run_full_sweep(s: Session | None = None) -> dict:
    from .space import get_disk_usage
    res = {}
    if s:
        res["retention"] = purge_expired(s)
    res["leftover_renders"] = cleanup_leftover_renders()
    res["disk_usage"] = get_disk_usage()
    return res
