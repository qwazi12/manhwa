"""Mathematical Pacing Engine, Scheduler, and Content Runway Meter.

Replaces basic cron schedules with a drift-free mathematical pacing throttle:
- 1/day to 48/day pacing presets
- Eliminates minute drift across daily posting windows
- Content runway meter (e.g. '34.4 days left')
- Per-pipeline and per-account overrides
- Pre-release momentum prioritization
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

try:
    from .ops_config import settings
except (ImportError, ValueError):
    from ops_config import settings
try:
    from .db import SessionLocal
except (ImportError, ValueError):
    from db import SessionLocal
try:
    from .models import AppSetting, QueueItem
except (ImportError, ValueError):
    from models import AppSetting, QueueItem

logger = logging.getLogger("manhwa.queue_manager")

DUE_GRACE = datetime.timedelta(minutes=30)
UTC = datetime.timezone.utc
TICK_SECONDS = 300
_wake = threading.Event()


def wake() -> None:
    """Wake the scheduler immediately on approvals or manual edits."""
    _wake.set()


def _aware(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def default_schedule() -> dict[str, Any]:
    return {
        "timezone": settings.post_timezone,
        "start_hour": settings.post_start_hour,
        "end_hour": settings.post_end_hour,
        "interval_hours": settings.post_interval_hours,
        "posts_per_day": settings.post_posts_per_day or 8,
        "pipelines": {},
        "accounts": {},
    }


def clean_times(raw: Any, where: str = "") -> list[str]:
    if isinstance(raw, str):
        raw = [t for t in raw.replace(";", ",").split(",")]
    out = set()
    for t in raw or []:
        t = str(t).strip()
        if not t:
            continue
        try:
            h, m = (int(x) for x in t.split(":"))
        except ValueError:
            raise ValueError(f"{where + ': ' if where else ''}'{t}' isn't a time like 10:00 or 18:30")
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"{where + ': ' if where else ''}'{t}' isn't a valid time")
        out.add(f"{h:02d}:{m:02d}")
    if len(out) > 48:
        raise ValueError(f"{where + ': ' if where else ''}at most 48 posting times a day")
    return sorted(out)


def validate_schedule(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        tz = str(cfg.get("timezone", settings.post_timezone))
        start = int(cfg.get("start_hour", settings.post_start_hour))
        end = int(cfg.get("end_hour", settings.post_end_hour))
        step = int(cfg.get("interval_hours", settings.post_interval_hours))
    except (KeyError, TypeError, ValueError):
        raise ValueError("timezone, start_hour, end_hour and interval_hours are required")
    try:
        ZoneInfo(tz)
    except Exception:
        raise ValueError(f"Unknown timezone: {tz}")
    if not (0 <= start <= 23 and 0 <= end <= 23):
        raise ValueError("Hours must be between 0 and 23")
    if start > end:
        raise ValueError("First slot must be at or before the last slot")
    if not (1 <= step <= 24):
        raise ValueError("Interval must be 1-24 hours")

    ppd = cfg.get("posts_per_day")
    if ppd is not None and ppd != "":
        try:
            ppd = int(ppd)
            if not (1 <= ppd <= 48):
                raise ValueError("posts_per_day must be between 1 and 48")
        except (TypeError, ValueError):
            raise ValueError("posts_per_day must be an integer between 1 and 48")
    else:
        ppd = None

    def _clean_overrides(raw_dict: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw_dict, dict):
            return {}
        cleaned = {}
        for k, v in raw_dict.items():
            if not isinstance(v, dict):
                continue
            entry = {}
            if v.get("posts_per_day") is not None and v.get("posts_per_day") != "":
                entry["posts_per_day"] = int(v["posts_per_day"])
            if v.get("start_hour") is not None and v.get("start_hour") != "":
                entry["start_hour"] = int(v["start_hour"])
            if v.get("end_hour") is not None and v.get("end_hour") != "":
                entry["end_hour"] = int(v["end_hour"])
            if v.get("interval_hours") is not None and v.get("interval_hours") != "":
                entry["interval_hours"] = int(v["interval_hours"])
            if v.get("times"):
                entry["times"] = clean_times(v["times"], k)
            if entry:
                cleaned[str(k)] = entry
        return cleaned

    raw_pipes = cfg.get("pipeline_overrides") or cfg.get("pipelines") or {}
    raw_accs = cfg.get("account_overrides") or cfg.get("accounts") or {}

    return {
        "timezone": tz,
        "start_hour": start,
        "end_hour": end,
        "interval_hours": step,
        "posts_per_day": ppd,
        "pipelines": _clean_overrides(raw_pipes),
        "accounts": _clean_overrides(raw_accs),
    }


_schedule_cfg: dict[str, Any] = default_schedule()


def schedule_config() -> dict[str, Any]:
    return dict(_schedule_cfg)


def load_schedule(s: Session) -> dict[str, Any]:
    global _schedule_cfg
    row = s.get(AppSetting, "schedule")
    try:
        _schedule_cfg = validate_schedule(row.value) if row and row.value else default_schedule()
    except ValueError as exc:
        logger.error("Stored schedule invalid (%s); using env defaults", exc)
        _schedule_cfg = default_schedule()
    return schedule_config()


def save_schedule(s: Session, cfg: dict[str, Any] | None) -> dict[str, Any]:
    row = s.get(AppSetting, "schedule")
    if cfg is None:
        if row:
            s.delete(row)
    else:
        cfg = validate_schedule(cfg)
        if row:
            row.value = cfg
        else:
            s.add(AppSetting(key="schedule", value=cfg))
    s.commit()
    after = load_schedule(s)
    wake()
    return after


def resolve_pacing(pipeline: str | None = None, accounts: list[str] | str | None = None) -> dict[str, Any]:
    cfg = _schedule_cfg
    tz = cfg["timezone"]
    start = cfg["start_hour"]
    end = cfg["end_hour"]
    step = cfg["interval_hours"]
    ppd = cfg.get("posts_per_day")

    override = None
    if accounts and cfg.get("accounts"):
        acc_list = [accounts] if isinstance(accounts, str) else accounts
        for acc in acc_list:
            clean = acc.split(":")[0].strip()
            if acc in cfg["accounts"]:
                override = cfg["accounts"][acc]
                break
            elif clean in cfg["accounts"]:
                override = cfg["accounts"][clean]
                break

    if not override and pipeline and cfg.get("pipelines"):
        if pipeline in cfg["pipelines"]:
            override = cfg["pipelines"][pipeline]

    if override:
        if override.get("posts_per_day") is not None:
            ppd = int(override["posts_per_day"])
        if override.get("start_hour") is not None:
            start = int(override["start_hour"])
        if override.get("end_hour") is not None:
            end = int(override["end_hour"])
        if override.get("interval_hours") is not None:
            step = int(override["interval_hours"])

    times = list((override or {}).get("times") or [])
    return {
        "timezone": tz,
        "start_hour": start,
        "end_hour": end,
        "interval_hours": step,
        "posts_per_day": len(times) if times else ppd,
        "times": times,
    }


def day_slots_for_pacing(
    day: datetime.date,
    tz: ZoneInfo,
    start_hour: int,
    end_hour: int,
    interval_hours: int,
    posts_per_day: int | None,
    times: list[str] | None = None,
) -> list[datetime.datetime]:
    """Calculates exact daytime slots mathematically, eliminating minute drift."""
    if times:
        out = []
        for t in times:
            h, m = (int(x) for x in t.split(":"))
            out.append(datetime.datetime.combine(day, datetime.time(h, m), tzinfo=tz))
        return out
    if posts_per_day is not None and posts_per_day > 0:
        if posts_per_day == 1:
            return [datetime.datetime.combine(day, datetime.time(start_hour), tzinfo=tz)]
        window_minutes = (end_hour - start_hour) * 60 if end_hour > start_hour else 1440
        slots = []
        for i in range(posts_per_day):
            mins = round(i * window_minutes / (posts_per_day - 1))
            total_mins = start_hour * 60 + mins
            h = min(23, total_mins // 60)
            m = total_mins % 60
            slots.append(datetime.datetime.combine(day, datetime.time(h, m), tzinfo=tz))
        return slots
    return [
        datetime.datetime.combine(day, datetime.time(h), tzinfo=tz)
        for h in range(start_hour, end_hour + 1, interval_hours)
    ]


def slots_after(
    t: datetime.datetime,
    n: int,
    pipeline: str | None = None,
    accounts: list[str] | str | None = None,
) -> list[datetime.datetime]:
    pacing = resolve_pacing(pipeline=pipeline, accounts=accounts)
    tz = ZoneInfo(pacing["timezone"])
    local = t.astimezone(tz)
    day = local.date()
    out: list[datetime.datetime] = []
    while len(out) < n:
        for slot in day_slots_for_pacing(
            day, tz,
            pacing["start_hour"], pacing["end_hour"],
            pacing["interval_hours"], pacing["posts_per_day"], pacing.get("times"),
        ):
            if slot > local:
                out.append(slot.astimezone(UTC))
                if len(out) == n:
                    break
        day += datetime.timedelta(days=1)
    return out


def plan_schedule(s: Session, now: datetime.datetime) -> int:
    """Plan and assign time slots for all Ready items according to pacing throttle."""
    ready = (
        s.query(QueueItem)
        .filter(QueueItem.status == "ready")
        .order_by(func.coalesce(QueueItem.position, QueueItem.id), QueueItem.id)
        .all()
    )
    if not ready:
        return 0

    from . import momentum

    # Sort ready items with pre-release / freshness momentum priority
    def _order_key(it: QueueItem) -> tuple:
        rel = momentum.extract_release_date(it, session=s)
        pos = it.position if it.position is not None else it.id
        if not rel:
            return (2, pos)
        m = momentum.compute_momentum(rel, today=now.date())
        days = m.get("days_until_release")
        if days is not None and days >= 0:
            return (0, days, pos)
        elif days is not None and days < 0:
            return (3, abs(days), pos)
        return (2, pos)

    ready.sort(key=_order_key)

    slots = slots_after(now, len(ready))
    changed = 0
    for it, slot in zip(ready, slots):
        if it.pinned_at is not None:
            continue
        if _aware(it.scheduled_at) != slot:
            it.scheduled_at = slot
            changed += 1
    if changed:
        s.commit()
    return changed


def get_schedule_info(s: Session) -> dict[str, Any]:
    """Return full schedule status including runway meter and slot pacing."""
    cfg = load_schedule(s)
    now = datetime.datetime.now(UTC)
    ready_count = s.query(QueueItem).filter(QueueItem.status == "ready").count()
    ppd = cfg.get("posts_per_day") or 8
    runway_days = round(ready_count / ppd, 1) if ppd > 0 else 0.0

    next_slots = [dt.isoformat() for dt in slots_after(now, 5)]

    return {
        "timezone": cfg["timezone"],
        "start_hour": cfg["start_hour"],
        "end_hour": cfg["end_hour"],
        "interval_hours": cfg["interval_hours"],
        "posts_per_day": ppd,
        "slots_per_day": ppd,
        "total_ready": ready_count,
        "runway_days": runway_days,
        "runway_label": f"{runway_days} days left ({ready_count} videos ready at {ppd}/day)",
        "next_slots": next_slots,
        "pipeline_overrides": cfg.get("pipelines", {}),
        "account_overrides": cfg.get("accounts", {}),
    }
