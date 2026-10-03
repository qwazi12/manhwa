"""Chapter Autopilot — ingest new chapters on its own, one at a time.

Owner request (2026-10-03): new chapters of every series on the Tracker should
simply appear in Projects, ready to review on the Board. The owner reviews and
approves (approve already renders + exports); everything can be stopped and
resumed.

How it picks (the owner's rules):
  * ALL watchlist series, in the watchlist's own order (tier, then rank).
  * Each series starts at its 3 LATEST chapters (fixed the first time the
    series is seen, so the window does not slide), then carries on in story
    order with every new release after that.
  * ROUND ROBIN: the next chapter goes to the series autopilot has made the
    fewest chapters for; ties go to the higher-ranked series. So every pass
    serves the top-ranked series first, then the rest.
  * At most `per_day` chapters a day (America/New_York day, the same day the
    spend cap uses), one at a time, only when the ingest queue is empty and
    one more chapter's estimate fits BOTH under autopilot's own daily budget
    (`budget_usd`, owner: $6 — counted from autopilot jobs' metered spend)
    and under the site-wide cap (MAX_DAILY_SPEND_USD, owner: $10). The budget
    decides whether a chapter STARTS; the site cap is the hard stop on every
    call, so a chapter started under budget can finish a little above it.
  * Gemini engine.

Never repeat: a ledger of every chapter autopilot queued or a finished ingest
produced (`_autopilot.json`). It survives project deletes, so a chapter the
owner deleted on purpose is not re-made. Chapters deleted BEFORE the ledger
existed are not in it (the owner asked to start from the latest 3 of every
series, which includes some of those).

Stop / resume, everywhere:
  * the whole autopilot: the ON/OFF switch (nothing new starts while OFF);
  * one series: pause / resume;
  * one chapter: Stop in Logs. A chapter the owner stopped is NOT picked
    again automatically — its series waits with "stopped by you" until the
    owner resumes it (Logs ▶) or presses Retry.
  * a chapter that fails twice blocks its series (story order) with the
    error, until Retry.

This module is pure decision logic over plain data. The server passes in the
live pieces (watchlist view, queue state, spend, enqueue) so tests drive it
without network or money.
"""

import json
import math
import os
import threading
import time
from datetime import datetime, timezone

STATE_NAME = "_autopilot.json"
DEFAULTS = {"enabled": False, "per_day": 4, "engine": "gemini", "window": 3,
            "paused_series": [], "budget_usd": 6.0}
MAX_PER_DAY = 20
MAX_BUDGET = 100.0          # $ a day autopilot may spend (owner: $6; site cap $10)
FAIL_LIMIT = 2              # a chapter failing this often blocks its series
FAIL_COOLDOWN = 3600        # seconds before a failed chapter is tried again
REFRESH_EVERY = 6 * 3600    # re-read every series page this often (free)
DEFAULT_ESTIMATE = 0.80     # $ per chapter until autopilot has history
TICK_SECONDS = 600          # scheduler cadence (server.py)
UNDO = ("Switch OFF: nothing new starts. A chapter already running finishes "
        "its current step — press Stop on it in Logs to halt it.")

# statuses a ledger entry can hold
LIVE = ("queued", "running", "paused", "pausing")
MADE = ("done",) + LIVE + ("budget_paused",)

_lock = threading.RLock()
runtime = {"last_tick": None, "last_result": None, "last_refresh": 0.0,
           "last_started": None}


# ------------------------------------------------------------------ helpers
def et_day(ts=None):
    """The America/New_York date for a timestamp (the spend cap's day)."""
    ts = time.time() if ts is None else ts
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def chap_key(c):
    """Story order for chapter ids ('2', '17.5', '358'). Non-numbers sort last."""
    try:
        f = float(str(c).strip())
        if math.isfinite(f):
            return (0, f, "")
    except (TypeError, ValueError):
        pass
    return (1, 0.0, str(c))


def norm_chapter(c):
    s = str(c).strip()
    try:
        f = float(s)
        return str(int(f)) if f.is_integer() else s
    except ValueError:
        return s


def ledger_key(canon_series_key, chapter):
    return f"{canon_series_key}|{norm_chapter(chapter)}"


# ------------------------------------------------------------------ state
def _path(root):
    return os.path.join(root, STATE_NAME)


def load(root):
    try:
        with open(_path(root), encoding="utf-8") as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    st.setdefault("settings", {})
    st["settings"] = {**DEFAULTS, **st["settings"]}
    st.setdefault("start_from", {})
    st.setdefault("ledger", {})
    return st


def save(root, st):
    tmp = _path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, _path(root))


def audit(event, **fields):
    """Structured line for every decision and change (rule 23/24)."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": "info",
           "service": "autopilot", "event": event, **fields}
    print(json.dumps(rec, default=str), flush=True)


def settings(root):
    return load(root)["settings"]


def update_settings(root, patch):
    """Validate and save. Returns (before, after)."""
    clean = {}
    if patch.get("enabled") is not None:
        clean["enabled"] = bool(patch["enabled"])
    if patch.get("per_day") is not None:
        v = int(patch["per_day"])
        if not 0 <= v <= MAX_PER_DAY:
            raise ValueError(f"chapters per day must be 0–{MAX_PER_DAY}")
        clean["per_day"] = v
    if patch.get("budget_usd") is not None:
        b = float(patch["budget_usd"])
        if not 0 <= b <= MAX_BUDGET:
            raise ValueError(f"autopilot budget must be $0–{MAX_BUDGET:.0f} a day")
        clean["budget_usd"] = round(b, 2)
    if patch.get("engine") is not None:
        if patch["engine"] != "gemini":
            raise ValueError("autopilot runs on Gemini only")
        clean["engine"] = "gemini"
    with _lock:
        st = load(root)
        before = dict(st["settings"])
        st["settings"].update(clean)
        save(root, st)
        after = dict(st["settings"])
    audit("settings_changed", before=before, after=after)
    return before, after


def set_series(root, series_id, action):
    """pause | resume | retry one series."""
    if action not in ("pause", "resume", "retry"):
        raise ValueError("action must be pause, resume or retry")
    with _lock:
        st = load(root)
        paused = list(st["settings"].get("paused_series") or [])
        if action == "pause" and series_id not in paused:
            paused.append(series_id)
        if action in ("resume", "retry"):
            paused = [p for p in paused if p != series_id]
        st["settings"]["paused_series"] = paused
        cleared = []
        if action in ("retry", "resume"):
            # a failed or owner-stopped chapter becomes pickable again
            for k, e in list(st["ledger"].items()):
                if e.get("series_id") == series_id and e.get("status") in ("failed", "stopped"):
                    cleared.append(e.get("chapter"))
                    del st["ledger"][k]
        save(root, st)
    audit("series_" + action, series=series_id, cleared=cleared)
    return {"paused": series_id in paused, "cleared": cleared}


# ------------------------------------------------------------------ ledger
def record_queued(root, key, *, series_id, chapter, project, job, source, url):
    with _lock:
        st = load(root)
        prev = st["ledger"].get(key) or {}
        st["ledger"][key] = {**prev, "series_id": series_id, "chapter": norm_chapter(chapter),
                             "project": project, "job": job, "source": source,
                             "url": url, "status": "queued",
                             "queued_at": prev.get("queued_at") or time.time(),
                             "updated_at": time.time(), "error": None,
                             "fails": prev.get("fails", 0)}
        save(root, st)


def on_job_end(root, job_id, status, error=None, cost=None):
    """Called when any ingest ends. Returns the entry it updated, if any."""
    with _lock:
        st = load(root)
        hit = None
        for k, e in st["ledger"].items():
            if e.get("job") == job_id:
                hit = k
                break
        if hit is None:
            return None
        e = st["ledger"][hit]
        e["updated_at"] = time.time()
        if status == "done":
            e.update(status="done", error=None)
            if cost is not None:
                e["cost"] = round(float(cost), 4)
        elif status == "cancelled":
            e.update(status="stopped", error=error or "stopped by you")
        elif status == "budget_paused":
            e.update(status="budget_paused", error=error)
        elif status == "error":
            e["fails"] = e.get("fails", 0) + 1
            e.update(status="failed", error=(error or "")[:300], failed_at=time.time())
        else:
            e["status"] = status
        save(root, st)
        entry = dict(e)
    audit("chapter_" + entry["status"], key=hit, job=job_id, error=entry.get("error"))
    return entry


def remember_done(root, key, *, series_id, chapter, project, job):
    """A finished ingest that autopilot did not queue (a manual one)."""
    with _lock:
        st = load(root)
        if st["ledger"].get(key, {}).get("status") == "done":
            return
        st["ledger"][key] = {"series_id": series_id, "chapter": norm_chapter(chapter),
                             "project": project, "job": job, "source": "manual",
                             "status": "done", "queued_at": time.time(),
                             "updated_at": time.time(), "fails": 0}
        save(root, st)


def set_status_for_job(root, job_id, status):
    """Keep the ledger in step when a job is resumed/re-queued."""
    with _lock:
        st = load(root)
        for e in st["ledger"].values():
            if e.get("job") == job_id:
                e["status"] = status
                e["updated_at"] = time.time()
                save(root, st)
                return True
    return False


def today_count(ledger, now=None):
    day = et_day(now)
    return sum(1 for e in ledger.values()
               if e.get("source") == "autopilot" and et_day(e.get("queued_at") or 0) == day)


def estimate(ledger):
    costs = [e["cost"] for e in sorted(ledger.values(), key=lambda e: e.get("updated_at") or 0)
             if e.get("source") == "autopilot" and e.get("status") == "done" and e.get("cost")]
    costs = costs[-5:]
    return round(sum(costs) / len(costs), 3) if costs else DEFAULT_ESTIMATE


# ------------------------------------------------------------------ candidates
def candidates(st, series_view, canon, live_projects=(), now=None):
    """One row per watchlist series, in watchlist order, with its next chapter
    and the reason it can or cannot be picked. Mutates st["start_from"] for
    series seen for the first time (the caller saves).

    series_view: watchlist.view()["series"]; canon: providers.canonical_key.
    live_projects: project ids with a queued/running ingest right now.
    """
    now = time.time() if now is None else now
    window = int(st["settings"].get("window") or 3)
    paused = set(st["settings"].get("paused_series") or [])
    ledger = st["ledger"]
    rows = []
    for idx, s in enumerate(series_view):
        sid = s["id"]
        best = next((m for m in s.get("mirrors") or [] if m.get("series_key") == s.get("best_mirror")), None)
        row = {"series_id": sid, "title": s.get("title"), "tier": s.get("tier"),
               "tier_label": s.get("tier_label"), "rank": s.get("rank"), "order": idx,
               "next": None, "remaining": [], "start_from": None, "state": "ready",
               "reason": "", "made_by_autopilot": 0, "mirror": None}
        if best is None:
            row.update(state="no_source", reason="no source attached")
            rows.append(row)
            continue
        row["mirror"] = {"source": best.get("source"), "series_key": best.get("series_key"),
                         "series_url": best.get("series_url"), "support": best.get("support"),
                         "status": best.get("status"), "last_checked": best.get("last_checked")}
        chapters = sorted({norm_chapter(c) for c in best.get("chapters") or []}, key=chap_key)
        if not chapters:
            row.update(state="no_source", reason=("source could not be read: " + (best.get("error") or ""))
                       if best.get("status") == "error" else "source not checked yet")
            rows.append(row)
            continue
        if best.get("support") not in ("supported", "partial"):
            row.update(state="no_source", reason=f"source support is {best.get('support')}")
            rows.append(row)
            continue
        if sid not in st["start_from"]:
            st["start_from"][sid] = chapters[-window] if len(chapters) >= window else chapters[0]
        start = st["start_from"][sid]
        row["start_from"] = start
        ck = canon(best["series_key"])
        have = {norm_chapter(c) for c in s.get("ingested") or []}
        mine = {k: e for k, e in ledger.items() if e.get("series_id") == sid or k.startswith(ck + "|")}
        row["made_by_autopilot"] = sum(1 for e in mine.values()
                                       if e.get("source") == "autopilot" and e.get("status") in MADE)
        todo = []
        for c in chapters:
            if chap_key(c) < chap_key(start) or c in have:
                continue
            e = ledger.get(ledger_key(ck, c))
            if e and e.get("status") in MADE:
                continue
            todo.append((c, e))
        row["remaining"] = [c for c, _ in todo]
        if not todo:
            row.update(state="up_to_date", reason="all caught up from ch." + str(start))
            rows.append(row)
            continue
        c, e = todo[0]
        row["next"] = c
        if sid in paused:
            row.update(state="paused", reason="paused by you")
        elif e and e.get("status") == "stopped":
            row.update(state="stopped", reason=f"ch.{c} was stopped by you — Retry or resume it in Logs")
        elif e and e.get("status") == "failed" and e.get("fails", 0) >= FAIL_LIMIT:
            row.update(state="blocked", reason=f"ch.{c} failed {e['fails']}× — {e.get('error') or 'error'}")
        elif e and e.get("status") == "failed" and now - (e.get("failed_at") or 0) < FAIL_COOLDOWN:
            mins = int((FAIL_COOLDOWN - (now - e.get("failed_at", 0))) / 60) + 1
            row.update(state="cooldown", reason=f"ch.{c} failed once; retrying in ~{mins} min")
        elif e and e.get("project") in live_projects:
            row.update(state="running", reason=f"ch.{c} is ingesting")
        else:
            row["reason"] = f"next: ch.{c}" + (f" · {len(todo) - 1} more after it" if len(todo) > 1 else "")
        rows.append(row)
    return rows


def pick(rows):
    """Round robin: fewest made by autopilot first, then watchlist order."""
    ready = [r for r in rows if r["state"] == "ready" and r["next"] is not None]
    if not ready:
        return None
    return min(ready, key=lambda r: (r["made_by_autopilot"], r["order"]))


# ------------------------------------------------------------------ decision
def decide(st, rows, *, queue_busy, spent, cap, now=None, ap_spent=0.0):
    """(waiting_reason or None, pick or None). No side effects."""
    cfg = st["settings"]
    if not cfg.get("enabled"):
        return "Autopilot is off", None
    if queue_busy:
        return "an ingest is running or waiting — autopilot adds the next one when the line is empty", None
    n, lim = today_count(st["ledger"], now), int(cfg.get("per_day") or 0)
    if n >= lim:
        return f"today's limit reached ({n} of {lim}) — continues after midnight ET", None
    est = estimate(st["ledger"])
    budget = float(cfg.get("budget_usd") or 0)
    if ap_spent + est > budget:
        return (f"autopilot budget used for today (${ap_spent:.2f} of ${budget:.2f}, "
                f"~${est:.2f} a chapter) — continues after midnight ET"), None
    if cap and spent + est > cap:
        return (f"not enough budget left today (${spent:.2f} spent, ~${est:.2f} a chapter, "
                f"cap ${cap:.2f}) — continues after midnight ET"), None
    p = pick(rows)
    if p is None:
        return "no series has a chapter ready (all caught up, paused or blocked)", None
    return None, p


def tick(root, deps, now=None):
    """One scheduler pass. Starts at most one chapter. Returns what it did.

    deps (all callables): view() -> series list, refresh(series_view) -> None,
    canon(key), live_projects() -> set, queue_busy() -> bool,
    spend() -> (spent, cap), chapter_url(row, chapter) -> url,
    project_id(url) -> str, enqueue(url, engine) -> job_id.
    """
    now = time.time() if now is None else now
    runtime["last_tick"] = now
    with _lock:
        st = load(root)
    if not st["settings"].get("enabled"):
        runtime["last_result"] = "off"
        return None
    if now - runtime["last_refresh"] > REFRESH_EVERY:
        runtime["last_refresh"] = now
        try:
            deps["refresh"]()
        except Exception as e:                     # stale lists beat no autopilot
            audit("refresh_failed", error=str(e)[:200])
    with _lock:
        st = load(root)
        rows = candidates(st, deps["view"](), deps["canon"], deps["live_projects"](), now)
        save(root, st)                              # start_from for new series
        spent, cap = deps["spend"]()
        reason, p = decide(st, rows, queue_busy=deps["queue_busy"](), spent=spent, cap=cap, now=now,
                           ap_spent=deps.get("ap_spend", lambda: 0.0)())
        if reason:
            runtime["last_result"] = "waiting: " + reason
            return None
        url = deps["chapter_url"](p, p["next"])
        pid = deps["project_id"](url)
        key = ledger_key(deps["canon"](p["mirror"]["series_key"]), p["next"])
        job = deps["enqueue"](url, st["settings"].get("engine", "gemini"))
        record_queued(root, key, series_id=p["series_id"], chapter=p["next"], project=pid,
                      job=job, source="autopilot", url=url)
    started = {"series": p["title"], "chapter": p["next"], "job": job, "project": pid, "at": now}
    runtime["last_started"] = started
    runtime["last_result"] = f"started {p['title']} ch.{p['next']}"
    audit("chapter_queued", **started)
    return started


def status(root, deps, now=None):
    """Rule 40 for the card: state, what happens next, when it last ran, undo."""
    now = time.time() if now is None else now
    with _lock:
        st = load(root)
        rows = candidates(st, deps["view"](), deps["canon"], deps["live_projects"](), now)
        save(root, st)
    spent, cap = deps["spend"]()
    ap_spent = deps.get("ap_spend", lambda: 0.0)()
    reason, p = decide(st, rows, queue_busy=deps["queue_busy"](), spent=spent, cap=cap, now=now,
                       ap_spent=ap_spent)
    nxt = p or (pick(rows) if st["settings"].get("enabled") else None)
    ledger = st["ledger"]
    recent = sorted((dict(e, key=k) for k, e in ledger.items() if e.get("source") == "autopilot"),
                    key=lambda e: e.get("updated_at") or 0, reverse=True)[:8]
    return {
        "settings": st["settings"],
        "enabled": bool(st["settings"].get("enabled")),
        "today": today_count(ledger, now), "per_day": st["settings"].get("per_day"),
        "day": et_day(now), "spent_usd": round(spent, 2), "cap_usd": cap,
        "ap_spent_usd": round(ap_spent, 2), "budget_usd": st["settings"].get("budget_usd"),
        "estimate_usd": estimate(ledger),
        "waiting": reason,
        "next": {"series": nxt["title"], "chapter": nxt["next"], "series_id": nxt["series_id"]} if nxt else None,
        "last_tick": runtime["last_tick"], "last_result": runtime["last_result"],
        "last_started": runtime["last_started"], "last_refresh": runtime["last_refresh"] or None,
        "tick_seconds": TICK_SECONDS,
        "series": rows, "recent": recent, "undo": UNDO,
    }
