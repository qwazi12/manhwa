"""Chapter-level release calendar (2026-11-14).

One row per chapter of each watched series: when the source had it, when the
studio noticed, made, reviewed, scheduled and — verified — published it, and
whether that met the release target (24–48 h from the moment a usable source
existed). Pure functions over plain data; the server gathers the inputs.

What is known and what is not, stated per row:
  source.available_at   the source's release date (DAY precision: counted from
                        00:00 UTC, so up to 24 h early) — else the time the
                        studio first saw the chapter (an UPPER bound: it may
                        have been out earlier) — else unknown
  source.basis          "release_date" | "first_seen" | "unknown"
  source.uncertain_hours  24 for a day-precision date; None when the true time
                        could be arbitrarily earlier (first_seen) or is unknown
A row with an unknown source time is never counted as on time or late."""
import statistics
from datetime import datetime, timezone

TARGET_HOURS = 48.0
GOAL_HOURS = 24.0
STEPS = ("source", "detected", "produced", "reviewed", "scheduled", "published")


def _day_start(day):
    try:
        return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def source_time(series, chapter):
    """{"available_at", "basis", "uncertain_hours", "detected_at"} for one chapter."""
    ch = str(chapter)
    day, seen = None, None
    for m in (series or {}).get("mirrors") or []:
        rd = (m.get("release_dates") or {}).get(ch)
        d = rd[0] if isinstance(rd, (list, tuple)) and rd else rd
        if isinstance(d, str) and (day is None or d < day):
            day = d
        fs = (m.get("first_seen") or {}).get(ch)
        if isinstance(fs, (int, float)) and (seen is None or fs < seen):
            seen = float(fs)
    at = _day_start(day)
    if at is not None:
        return {"available_at": at, "basis": "release_date", "uncertain_hours": 24.0, "detected_at": seen}
    if seen is not None:
        return {"available_at": seen, "basis": "first_seen", "uncertain_hours": None, "detected_at": seen}
    return {"available_at": None, "basis": "unknown", "uncertain_hours": None, "detected_at": None}


def _state(row, now):
    src, live = row["source"]["available_at"], row["published"].get("at")
    if row["source"]["basis"] == "unknown":
        return "published_source_time_unknown" if live else "source_time_unknown"
    if live:
        h = (live - src) / 3600.0
        row["hours_to_live"] = round(h, 1)
        return "on_time" if h <= TARGET_HOURS else "late"
    left = TARGET_HOURS - (now - src) / 3600.0
    row["hours_left"] = round(left, 1)
    return "due" if left >= 0 else "overdue"


def _next_step(row):
    if row["published"].get("at"):
        return None
    if row["scheduled"].get("at"):
        return "waiting for its slot"
    if row["reviewed"].get("status") == "approved":
        return "schedule it"
    if row["produced"].get("at"):
        return "review it" if not row["produced"].get("included_in") else "review the compilation"
    st = row["produced"].get("status")
    if st in ("queued", "running", "paused", "held"):
        return "being made"
    if st in ("failed", "blocked"):
        return "fix the failed chapter"
    return "make it"


def build(series_list, chapter_state, now, channel_of=None, since_days=30, per_series=12):
    """series_list: watchlist series [{"id", "title", "mirrors": [...]}].
    chapter_state(series_id, chapter) -> {"produced": {...}, "reviewed": {...},
      "scheduled": {...}, "published": {...}} (missing keys = not yet).
    channel_of(series_id) -> channel id or None.
    Rows: the newest `per_series` chapters of each series whose source time is
    within `since_days`, or is unknown, or which are not published yet."""
    rows = []
    for s in series_list or []:
        chapters = set()
        for m in s.get("mirrors") or []:
            chapters.update(str(c) for c in m.get("chapters") or [])
            chapters.update((m.get("release_dates") or {}).keys())
            chapters.update((m.get("first_seen") or {}).keys())

        def key(c):
            try:
                return (0, float(c))
            except ValueError:
                return (1, 0.0)
        for ch in sorted(chapters, key=key, reverse=True)[:per_series]:
            st = chapter_state(s.get("id"), ch) or {}
            row = {"series_id": s.get("id"), "series": s.get("title") or s.get("id"), "chapter": ch,
                   "channel": channel_of(s.get("id")) if channel_of else None,
                   "source": source_time(s, ch)}
            row["detected"] = {"at": row["source"].pop("detected_at")}
            for step in ("produced", "reviewed", "scheduled", "published"):
                row[step] = dict(st.get(step) or {})
            src = row["source"]["available_at"]
            if src is not None and now - src > since_days * 86400 and row["published"].get("at"):
                continue
            row["state"] = _state(row, now)
            row["next"] = _next_step(row)
            rows.append(row)
    rows.sort(key=lambda r: (r["published"].get("at") is not None,
                             r.get("hours_left", 1e9) if not r["published"].get("at") else -r["published"]["at"]))
    return {"generated_at": now, "target_hours": TARGET_HOURS, "goal_hours": GOAL_HOURS,
            "rows": rows, "summary": summarize(rows, now)}


def summarize(rows, now):
    live = [r for r in rows if r["published"].get("at")]
    timed = [r for r in live if "hours_to_live" in r]
    hours = sorted(r["hours_to_live"] for r in timed)
    by_state = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    per_day = {}
    for r in live:
        if now - r["published"]["at"] <= 7 * 86400:
            day = datetime.fromtimestamp(r["published"]["at"], timezone.utc).strftime("%Y-%m-%d")
            per_day.setdefault(day, set()).add(r["published"].get("video") or (r["series_id"], r["chapter"]))
    channels = {}
    for r in rows:
        c = channels.setdefault(r.get("channel") or "unassigned", {"chapters": 0, "published": 0, "open": 0, "overdue": 0})
        c["chapters"] += 1
        c["published"] += bool(r["published"].get("at"))
        c["open"] += not r["published"].get("at")
        c["overdue"] += r["state"] == "overdue"
    released_7d = sum(len(v) for v in per_day.values())
    return {
        "chapters": len(rows), "by_state": by_state,
        "measured": len(timed),
        "unmeasured": len(live) - len(timed),          # published, source time unknown: not counted either way
        "within_24h": sum(h <= GOAL_HOURS for h in hours), "within_48h": sum(h <= TARGET_HOURS for h in hours),
        "within_48h_pct": round(100.0 * sum(h <= TARGET_HOURS for h in hours) / len(hours), 1) if hours else None,
        "median_hours": round(statistics.median(hours), 1) if hours else None,
        "day_precision": sum(1 for r in timed if r["source"]["basis"] == "release_date"),
        "from_first_seen": sum(1 for r in timed if r["source"]["basis"] == "first_seen"),
        "releases_last_7_days": released_7d, "releases_per_day_7d": round(released_7d / 7.0, 2),
        "releases_by_day": {d: len(v) for d, v in sorted(per_day.items())},
        "by_channel": channels,
        "note": "a release date has day precision (counted from 00:00 UTC, so a chapter can look up to 24 h "
                "later than it was); 'first seen' is when the studio first read the chapter and may be later "
                "than its real release; chapters with neither are listed but not scored"}
