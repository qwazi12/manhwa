"""Chapter Release Momentum and Audience Intelligence for Manhwa Recap Studio.

Tracks chapter release dates, raw releases, and next-chapter drop schedules:
- 🚨 Critical / Urgent: 0–3 days before release (peak pre-release buzz)
- ⚡ Approaching / Optimal: 4–14 days before release
- 🗓️ Upcoming: >14 days before release
- ⚠️ Released / Stale: <0 days (warns if video is scheduled after next chapter or missed window)
"""

from __future__ import annotations

import datetime
from typing import Any

UTC = datetime.timezone.utc


def parse_date(val: Any) -> datetime.date | None:
    if not val:
        return None
    if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
        return val
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            return datetime.date.fromisoformat(val[:10])
        except (ValueError, IndexError):
            pass
    return None


def parse_datetime(val: Any) -> datetime.datetime | None:
    if not val:
        return None
    if isinstance(val, datetime.datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=UTC)
        return val
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            cleaned = val.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            pass
    return None


def extract_release_date(item_or_project: Any, session: Any = None) -> datetime.date | None:
    if item_or_project is None:
        return None

    # 1. From facts dict (e.g. StudioProject)
    if hasattr(item_or_project, "facts") and isinstance(item_or_project.facts, dict):
        facts = item_or_project.facts
        d = parse_date(facts.get("release_date") or facts.get("next_chapter_date"))
        if d:
            return d

    # 2. From research dict (e.g. QueueItem or StudioProject)
    if hasattr(item_or_project, "research") and isinstance(item_or_project.research, dict):
        res = item_or_project.research
        d = parse_date(res.get("release_date") or res.get("next_chapter_date"))
        if d:
            return d

    return None


def compute_momentum(
    release_date: datetime.date | str | None,
    scheduled_at: datetime.datetime | str | None = None,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Calculate release momentum metrics for a manhwa chapter."""
    rel = parse_date(release_date)
    today = today or datetime.date.today()
    sched = parse_datetime(scheduled_at)

    if not rel:
        return {
            "has_release_date": False,
            "release_date": None,
            "days_until_release": None,
            "status": "unknown",
            "urgency": "none",
            "is_pre_release": False,
            "is_post_release": False,
            "scheduled_after_release": False,
            "badge_text": "Date TBA",
            "badge_variant": "neutral",
            "warning": None,
            "priority_score": 0,
        }

    days = (rel - today).days
    rel_iso = rel.isoformat()

    sched_date = sched.date() if sched else None
    scheduled_after = (sched_date > rel) if sched_date else False

    if days < 0:
        status = "released"
        urgency = "missed"
        badge_variant = "missed"
        badge_text = f"⚠️ Released {abs(days)}d ago"
        priority_score = -100 - min(abs(days), 500)
    elif days == 0:
        status = "critical"
        urgency = "urgent"
        badge_variant = "critical"
        badge_text = "🚨 Drops TODAY (Peak Momentum)"
        priority_score = 1000
    elif 1 <= days <= 3:
        status = "critical"
        urgency = "urgent"
        badge_variant = "critical"
        badge_text = f"🚨 Drops in {days}d (URGENT)"
        priority_score = 1000 - days
    elif 4 <= days <= 14:
        status = "approaching"
        urgency = "optimal"
        badge_variant = "approaching"
        badge_text = f"⚡ Drops in {days}d (Prime Window)"
        priority_score = 800 - days
    else:
        status = "upcoming"
        urgency = "early"
        badge_variant = "upcoming"
        badge_text = f"🗓️ Next chapter in {days}d"
        priority_score = 500 - min(days, 300)

    warning = None
    if scheduled_after:
        sched_str = sched_date.strftime("%b %d, %Y") if sched_date else ""
        rel_str = rel.strftime("%b %d, %Y")
        warning = f"⚠️ Scheduled for {sched_str}, AFTER next chapter drop ({rel_str})! Post before drop to capture search momentum."
    elif days < 0 and abs(days) > 7:
        warning = f"Chapter released {abs(days)} days ago. Freshness window is decaying."

    return {
        "has_release_date": True,
        "release_date": rel_iso,
        "days_until_release": days,
        "status": status,
        "urgency": urgency,
        "is_pre_release": days >= 0,
        "is_post_release": days < 0,
        "scheduled_after_release": scheduled_after,
        "badge_text": badge_text,
        "badge_variant": badge_variant,
        "warning": warning,
        "priority_score": priority_score,
    }
