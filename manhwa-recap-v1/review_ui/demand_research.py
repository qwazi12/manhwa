"""Demand research (owner plan, 2026-10-04, step 6) — suggestions only.

Once a week, for every Tracker series: search YouTube for recaps of it and
measure how they do. The signal is the median VIEWS PER DAY of the matching
recaps (a 2-year-old video with 1M views and a 2-week-old one with 100k are
very different signals), plus how many were posted in the last 90 days (how
crowded the topic is).

It never changes anything by itself: each series gets a suggested tier
("Make now / Next up / Watching") and the owner applies it with one tap.

Costs nothing in money; YouTube quota per series is 100 units (search) + 1
(stats), so 14 series ≈ 1,414 of the 10,000 daily units, once a week.
"""

import json
import os
import re
import statistics
import threading
import time

NAME = "_demand.json"
EVERY_DAYS = 7
RECENT_DAYS = 90
HIGH_VPD = 500          # median views/day of matching recaps
MEDIUM_VPD = 100
TIER_FOR = {"high": "greenlight", "medium": "high_upside", "low": "watchlist"}
_lock = threading.Lock()
STATUS = {"running": False}

_STOP = set("the a an of in on to and is my i you his her their at for with from as by".split())


def _path(root):
    return os.path.join(root, NAME)


def load(root):
    try:
        with open(_path(root), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"at": None, "series": {}}


def _save(root, d):
    tmp = _path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _path(root))


def _words(s):
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in _STOP and len(w) > 1]


def matches(video_title, names):
    """Is this video about the series? Most of the distinctive words of the
    title (or of one alias) appear in the video's title."""
    vt = set(_words(video_title))
    for n in names:
        ws = _words(n)
        if ws and sum(w in vt for w in ws) / len(ws) >= 0.6:
            return True
    return False


def _age_days(iso, now):
    from datetime import datetime
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
    return max(1.0, (now - t) / 86400.0)


def measure(series, client, now):
    """One series: search, stats, the numbers and the suggestion."""
    names = [series["title"]] + list(series.get("aliases") or [])
    found = client.search_recaps(f"{series['title']} manhwa recap", limit=15)
    hits = [v for v in found if v.get("video_id") and matches(v.get("title"), names)]
    stats = client.video_stats([v["video_id"] for v in hits]) if hits else {}
    rows = []
    for v in hits:
        st = stats.get(v["video_id"])
        age = _age_days(v.get("published_at"), now)
        if not st or not age:
            continue
        rows.append({"title": v["title"][:120], "channel": v.get("channel"), "video_id": v["video_id"],
                     "views": st["views"], "age_days": round(age), "vpd": round(st["views"] / age, 1)})
    rows.sort(key=lambda r: -r["vpd"])
    if not rows:
        level, vpd = "unknown", None
    else:
        vpd = statistics.median(r["vpd"] for r in rows)
        level = "high" if vpd >= HIGH_VPD else "medium" if vpd >= MEDIUM_VPD else "low"
    suggest = TIER_FOR.get(level)
    return {"level": level, "median_vpd": vpd, "recaps": len(rows),
            "recent": sum(1 for r in rows if r["age_days"] <= RECENT_DAYS),
            "top": rows[:3], "suggested_tier": suggest,
            "differs": bool(suggest and suggest != series.get("tier")),
            "searched": len(found), "at": now}


def run(root, series_list, client, now=None):
    """Research every series; one failure doesn't stop the rest."""
    now = now or time.time()
    with _lock:
        if STATUS.get("running"):
            raise RuntimeError("demand research is already running")
        STATUS.update(running=True, started=now, done=0, total=len(series_list), error=None)
    out = load(root)
    out.setdefault("series", {})
    try:
        for s in series_list:
            try:
                out["series"][s["id"]] = measure(s, client, now)
            except Exception as e:
                out["series"][s["id"]] = {"level": "error", "error": str(e)[:200], "at": now}
            STATUS["done"] += 1
        out["at"] = now
        out["quota_used"] = getattr(client, "spent", None)
        _save(root, out)
        return out
    finally:
        STATUS["running"] = False


def due(root, now=None):
    at = load(root).get("at")
    return not at or (now or time.time()) - at >= EVERY_DAYS * 86400
