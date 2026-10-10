"""SQLAlchemy engine, session factory, migrations, and disk synchronization."""

from __future__ import annotations

import json
import logging
import os
import pathlib
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

try:
    from .ops_config import settings
except (ImportError, ValueError):
    from ops_config import settings
try:
    from .models import Base, StudioProject, QueueItem, ResumableJob, UndoEntry, AppSetting, UsageEvent
except (ImportError, ValueError):
    from models import Base, StudioProject, QueueItem, ResumableJob, UndoEntry, AppSetting, UsageEvent

logger = logging.getLogger("manhwa.db")

_url = settings.resolved_database_url
if _url.startswith("sqlite"):
    engine = create_engine(
        _url,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
else:
    engine = create_engine(_url, pool_pre_ping=True, pool_size=10, max_overflow=20, pool_timeout=15)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Initialize tables, take pre-migration backup, and apply additive migrations."""
    try:
        from . import backup
    except (ImportError, ValueError):
        import backup

    Base.metadata.create_all(engine)
    try:
        backup.create_pre_migration_backup()
    except Exception as exc:
        logger.warning("Pre-migration backup skipped: %s", exc)
    _migrate()
    sync_projects_from_disk()


def _migrate() -> None:
    """Idempotent additive schema updates."""
    insp = inspect(engine)
    tables = insp.get_table_names()

    if "studio_projects" in tables:
        cols = {c["name"] for c in insp.get_columns("studio_projects")}
        with engine.begin() as conn:
            if "archive" not in cols:
                conn.execute(text("ALTER TABLE studio_projects ADD COLUMN archive JSON"))
            if "cost_usd" not in cols:
                conn.execute(text("ALTER TABLE studio_projects ADD COLUMN cost_usd FLOAT DEFAULT 0.0"))

    if "queue_items" in tables:
        cols = {c["name"] for c in insp.get_columns("queue_items")}
        with engine.begin() as conn:
            if "position" not in cols:
                conn.execute(text('ALTER TABLE queue_items ADD COLUMN "position" INTEGER'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS ix_queue_items_position ON queue_items ("position")'))
            if "publish_requests" not in cols:
                conn.execute(text("ALTER TABLE queue_items ADD COLUMN publish_requests JSON"))
            if "media_url" not in cols:
                conn.execute(text("ALTER TABLE queue_items ADD COLUMN media_url TEXT"))
            if "pinned_at" not in cols:
                conn.execute(text("ALTER TABLE queue_items ADD COLUMN pinned_at TIMESTAMP WITH TIME ZONE"))
            conn.execute(text('UPDATE queue_items SET "position" = id WHERE "position" IS NULL'))


def sync_projects_from_disk() -> int:
    """Bi-directional compatibility: discover existing disk projects and sync into SQLite."""
    try:
        try:
            from . import ingest
        except (ImportError, ValueError):
            import ingest
        projects_dir = pathlib.Path(ingest.PROJECTS)
        if not projects_dir.is_dir():
            return 0
    except Exception:
        return 0

    synced = 0
    with SessionLocal() as s:
        for p_path in projects_dir.iterdir():
            if not p_path.is_dir() or p_path.name.startswith(".") or p_path.name.startswith("_"):
                continue
            pj_file = p_path / "project.json"
            if not pj_file.is_file():
                continue
            try:
                data = json.loads(pj_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            pid = str(data.get("id") or p_path.name)
            existing = s.query(StudioProject).filter(StudioProject.project_id == pid).first()
            if not existing:
                # Stage detection from project structure
                stage = "new"
                if (p_path / "segments.json").is_file():
                    stage = "match"
                elif (p_path / "script.json").is_file() or (p_path / "narration.json").is_file():
                    stage = "script"
                elif (p_path / "panels.json").is_file() or (p_path / "storyboard.json").is_file():
                    stage = "describe"
                elif (p_path / "crops").is_dir() or (p_path / "panels").is_dir():
                    stage = "split"

                render_info = None
                export_mp4 = p_path / "exports" / "final.mp4"
                if export_mp4.is_file():
                    stage = "render"
                    render_info = {
                        "file": str(export_mp4),
                        "size": export_mp4.stat().st_size,
                    }

                sp = StudioProject(
                    project_id=pid,
                    series_slug=str(data.get("series") or ""),
                    chapter_number=float(data.get("chapter") or 1.0),
                    title=str(data.get("title") or pid),
                    stage=stage,
                    stage_status="done" if render_info else "idle",
                    render=render_info,
                    facts=data.get("bible"),
                    research=data.get("seo"),
                )
                s.add(sp)
                synced += 1
        if synced:
            s.commit()
            logger.info("Synced %d existing projects from disk into database.", synced)
    return synced


async def get_session() -> AsyncIterator[Session]:
    """FastAPI dependency yielding a session, closing asynchronously to avoid thread deadlock."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
