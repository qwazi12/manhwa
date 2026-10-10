"""SQLAlchemy models for Manhwa Recap Studio.

Structured relational models matching and exceeding Scrapper's data layer:
- StudioProject: tracks each chapter's lifecycle from scrape to render with per-stage JSON columns.
- QueueItem: social queue publishing with multi-platform accounts and positioning.
- ResumableJob: durable record of long running ops (renders, ingests, backups) surviving restarts.
- UndoEntry: full reversibility snapshot engine.
- AppSetting: persistent operator settings (pacing throttle, posting schedule overrides).
- UsageEvent: granular cost and token metering.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class Base(DeclarativeBase):
    pass


class StudioProject(Base):
    """One manhwa recap chapter project and every stage's output.

    Each stage writes its result into its own JSON column (and files under projects/<id>/),
    so any stage can be re-run without redoing previous stages.
    """
    __tablename__ = "studio_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    series_slug: Mapped[str] = mapped_column(String(128), index=True, default="")
    chapter_number: Mapped[float] = mapped_column(Float, default=1.0)
    title: Mapped[str] = mapped_column(String(255), default="")
    target_minutes: Mapped[float] = mapped_column(Float, default=10.0)

    # Runner state: which stage is running / last ran, and how it went.
    # Stages: scrape | split | describe | narrate | voice | match | render
    stage: Mapped[str] = mapped_column(String(32), default="new")
    stage_status: Mapped[str] = mapped_column(String(16), default="idle")  # idle | running | queued | done | error | stopped | paused
    stage_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # Bible, series defaults, OCR lines
    research: Mapped[dict | None] = mapped_column(JSON, nullable=True)    # SEO findings, release momentum, metadata
    shots: Mapped[list | None] = mapped_column(JSON, nullable=True)       # Extracted & classified panels
    script: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # Script sentences, dialogue, word counts
    plan: Mapped[list | None] = mapped_column(JSON, nullable=True)        # DP match segments: audio beat -> panel crop
    render: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # Final video info: file, seconds, size, clips
    queue_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    archive: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # Retention: archived_at, freed_mb, restored_at
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class QueueItem(Base):
    """One video queued for syndication across shorts and social platforms."""
    __tablename__ = "queue_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    series_slug: Mapped[str] = mapped_column(String(128), default="", index=True)
    chapter_number: Mapped[float | None] = mapped_column(Float, nullable=True)
    pipeline: Mapped[str] = mapped_column(String(64), default="Manhwa Recaps", index=True)
    video_name: Mapped[str] = mapped_column(String(255), default="")
    video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumb_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)

    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")

    accounts: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["default:*", "mk:youtube"]
    status: Mapped[str] = mapped_column(String(32), default="review", index=True)  # review|ready|posting|posted|retry|error|archived
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    research: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Release momentum, chapter dates
    position: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    pinned_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_requests: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Multi-platform request IDs & URLs
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ResumableJob(Base):
    """A background job recorded in SQLite so a restart or deploy can't silently lose it."""
    __tablename__ = "resumable_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # studio | render | ingest | backup | sweep
    label: Mapped[str] = mapped_column(String(255), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)  # running|done|stopped|failed|budget_paused
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str | None] = mapped_column(String(40), nullable=True)  # process UUID heartbeat
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UndoEntry(Base):
    """Snapshot of rows before a mutation, restored as-is upon Undo."""
    __tablename__ = "undo_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), index=True)  # queue | studio:<id> | settings
    label: Mapped[str] = mapped_column(String(255), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class AppSetting(Base):
    """Owner-editable settings that override env defaults (e.g. pacing throttle)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UsageEvent(Base):
    """Granular cost and token tracking record."""
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    service: Mapped[str] = mapped_column(String(32), index=True)  # gemini | tts | upload-post | drive
    operation: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    chars: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
