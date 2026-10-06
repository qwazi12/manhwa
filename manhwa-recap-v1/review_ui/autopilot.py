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


_SAY = {
    "chapter_queued": lambda f: (f"picked {f.get('series')} ch.{f.get('chapter')} ({f.get('why') or 'round robin'})", "info"),
    "priority_add": lambda f: (f"Scheduled for processing: added {', '.join('ch.' + str(c) for c in f.get('chapters') or [])} of {f.get('series')} ({f.get('n')} in the list)", "info"),
    "priority_remove": lambda f: (f"Scheduled for processing: removed {', '.join('ch.' + str(c) for c in f.get('chapters') or [])} of {f.get('series')}", "info"),
    "priority_top": lambda f: (f"Scheduled for processing: moved {f.get('series')} ch.{(f.get('chapters') or [''])[0]} to the top", "info"),
    "priority_set": lambda f: ("Scheduled for processing: order changed", "info"),
    "priority_clear": lambda f: ("Scheduled for processing: list cleared", "info"),
    "priority_mix": lambda f: (f"Scheduled for processing: mixed ({f.get('mode')}, {f.get('n')} chapters)", "info"),
    "priority_undo": lambda f: ("Scheduled for processing: last change undone", "info"),
    "priority_group_top": lambda f: (f"Scheduled for processing: {f.get('series')} moved to the top", "info"),
    "priority_group_remove": lambda f: (f"Scheduled for processing: {f.get('series')} removed", "info"),
    "chapter_requested": lambda f: (f"made on request: {f.get('series')} ch.{f.get('chapter')}", "info"),
    "chapter_done": lambda f: (f"{f.get('key')} done", "ok"),
    "chapter_failed": lambda f: (f"{f.get('key')} failed: {f.get('error')}", "error"),
    "chapter_stopped": lambda f: (f"{f.get('key')} stopped by you", "warn"),
    "chapter_budget_paused": lambda f: (f"{f.get('key')} paused by the spend cap", "warn"),
    "settings_changed": lambda f: ("settings: " + ", ".join(
        f"{k} {f.get('before', {}).get(k)} → {v}" for k, v in (f.get("after") or {}).items()
        if (f.get("before") or {}).get(k) != v) or "settings saved", "info"),
    "backfill_planned": lambda f: (f"{f.get('series')}: back catalogue planned from ch.{f.get('after')}", "info"),
    "refresh_failed": lambda f: (f"reading series pages failed: {f.get('error')}", "warn"),
    "project_archived": lambda f: (f"archived {f.get('project')} ({f.get('reason')})", "info"),
    "archived_project_deleted": lambda f: (f"deleted archived {f.get('project')} (freed {f.get('freed_mb')} MB)", "warn"),
}


def audit(event, **fields):
    """Structured line for every decision and change (rule 23/24), and the same
    decision in plain words on the Logs → Live feed."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": "info",
           "service": "autopilot", "event": event, **fields}
    print(json.dumps(rec, default=str), flush=True)
    try:
        import events
        say = _SAY.get(event)
        msg, lvl = say(fields) if say else (event.replace("_", " ") + " " + ", ".join(
            f"{k}={v}" for k, v in fields.items() if isinstance(v, (str, int, float)))[:200], "info")
        kind = "archive" if event.startswith(("project_", "archived_")) else "autopilot"
        events.emit(kind, msg, lvl)
    except Exception:
        pass


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


# ------------------------------------------------------------------ Make next
# Owner, 2026-10-05: "pick what videos the autopilot should do next — check the
# things I want done, in order, and it works through them before the others".
# An ordered list in settings["priority"]: [{series_id, chapter, added_at}].
# The first READY entry beats round robin. Same limits as everything else
# (on/off, chapters a day, autopilot budget, site cap); a made chapter leaves
# the list by itself; a failing one is skipped (with the reason) so it never
# holds up the rest.
MAX_PRIORITY = 200


def _prio(st):
    return list(st["settings"].get("priority") or [])


def priority_edit(root, action, series_id="", chapters=(), order=None, mode="round_robin"):
    """add (append, in the order given) | remove | top | clear | set (full order).
    Returns the new list."""
    with _lock:
        st = load(root)
        cur = _prio(st)
        keyf = lambda e: (e["series_id"], norm_chapter(e["chapter"]))
        if action == "add":
            have = {keyf(e) for e in cur}
            for c in chapters:
                k = (series_id, norm_chapter(c))
                if k not in have:
                    cur.append({"series_id": series_id, "chapter": k[1], "added_at": time.time()})
                    have.add(k)
            if len(cur) > MAX_PRIORITY:
                raise ValueError(f"the scheduled list holds at most {MAX_PRIORITY} chapters")
        elif action == "remove":
            drop = {(series_id, norm_chapter(c)) for c in chapters}
            cur = [e for e in cur if keyf(e) not in drop]
        elif action == "top":
            k = (series_id, norm_chapter((list(chapters) or [""])[0]))
            cur = [e for e in cur if keyf(e) == k] + [e for e in cur if keyf(e) != k]
        elif action == "clear":
            cur = []
        elif action == "mix":
            if mode not in ("round_robin", "by_series", "random"):
                raise ValueError("mix mode must be round_robin, by_series or random")
            cur = mix(cur, mode)
        elif action == "undo":
            prev = st["settings"].get("priority_prev")
            if prev is None:
                raise ValueError("nothing to undo")
            cur, prev_now = prev, cur
        elif action == "group_top":
            cur = [e for e in cur if e["series_id"] == series_id] + [e for e in cur if e["series_id"] != series_id]
        elif action == "group_remove":
            cur = [e for e in cur if e["series_id"] != series_id]
        elif action == "set":
            by = {keyf(e): e for e in cur}
            new = [by[(o["series_id"], norm_chapter(o["chapter"]))] for o in (order or [])
                   if (o.get("series_id"), norm_chapter(o.get("chapter"))) in by]
            cur = new + [e for e in cur if e not in new]
        else:
            raise ValueError("action must be add, remove, top, clear, set, mix, undo, group_top or group_remove")
        if action in ("mix", "clear", "set", "group_top", "group_remove"):
            st["settings"]["priority_prev"] = _prio(st)       # one step of Undo
        elif action == "undo":
            st["settings"]["priority_prev"] = prev_now
        st["settings"]["priority"] = cur
        save(root, st)
    audit("priority_" + action, series=series_id, chapters=list(chapters), n=len(cur), mode=mode)
    return cur


def mix(entries, mode="round_robin", rnd=None):
    """Scrapper's Mix & Shuffle, for chapters (owner, 2026-10-05).
    round_robin: one chapter from each series in turn, series in random order,
                 each series' chapters kept in STORY order (never chapter 5 before 4);
    by_series:   all of one series, then the next (series order shuffled);
    random:      a full shuffle, then each series' chapters put back in story
                 order in the slots that series landed on."""
    import random
    r = rnd or random.Random()
    by = {}
    for e in entries:
        by.setdefault(e["series_id"], []).append(e)
    for v in by.values():
        v.sort(key=lambda e: chap_key(e["chapter"]))
    series = list(by)
    r.shuffle(series)
    if mode == "by_series":
        return [e for sid in series for e in by[sid]]
    if mode == "random":
        slots = [e["series_id"] for e in entries]
        r.shuffle(slots)
        it = {sid: iter(v) for sid, v in by.items()}
        return [next(it[sid]) for sid in slots]
    out, i = [], 0
    while len(out) < len(entries):
        for sid in series:
            if i < len(by[sid]):
                out.append(by[sid][i])
        i += 1
    return out


def priority_rows(st, series_view, canon, live_projects=(), now=None):
    """The Make next list with each entry's state; prunes made chapters from st.
    Each row: series_id, title, chapter, state (ready|running|waiting|blocked|
    no_source), reason, and — when ready — the candidates-style row to start."""
    now = time.time() if now is None else now
    view = {s["id"]: s for s in series_view}
    ledger = st["ledger"]
    out, keep = [], []
    for e in _prio(st):
        sid, ch = e["series_id"], norm_chapter(e["chapter"])
        s = view.get(sid)
        row = {"series_id": sid, "chapter": ch, "title": (s or {}).get("title") or sid,
               "state": "ready", "reason": "", "start": None}
        if s is None:
            row.update(state="no_source", reason="this series is no longer tracked")
            keep.append(e); out.append(row); continue
        cand = candidates({**st, "start_from": dict(st["start_from"])}, [s], canon, live_projects, now)[0]
        best = next((m for m in s.get("mirrors") or [] if m.get("series_key") == s.get("best_mirror")), None)
        if cand["mirror"] is None or best is None:
            row.update(state="no_source", reason=cand["reason"] or "no readable source")
            keep.append(e); out.append(row); continue
        listed = {norm_chapter(c) for c in best.get("chapters") or []}
        le = ledger.get(ledger_key(canon(best["series_key"]), ch))
        made = ch in {norm_chapter(c) for c in s.get("ingested") or []} or (le and le.get("status") == "done")
        if made:
            continue                                   # done: leaves the list
        keep.append(e)
        if le and le.get("status") == "budget_paused":
            row.update(state="waiting", reason="paused by a usage limit — resumes after midnight ET, "
                                               "or Resume it in the jobs bar")
        elif le and le.get("status") in LIVE:
            row.update(state="running", reason="being made now")
        elif ch not in listed:
            row.update(state="waiting", reason=f"not on the source yet (latest there is ch.{best.get('latest')})")
        elif le and le.get("status") == "stopped":
            row.update(state="blocked", reason="stopped by you — remove it or Retry the series")
        elif le and le.get("status") == "failed" and le.get("fails", 0) >= FAIL_LIMIT:
            row.update(state="blocked", reason=f"failed {le['fails']}× — {le.get('error') or 'error'}")
        elif le and le.get("status") == "failed" and now - (le.get("failed_at") or 0) < FAIL_COOLDOWN:
            row.update(state="waiting", reason="failed once; tries again within the hour")
        else:
            row["start"] = dict(cand, next=ch, priority=True)
        out.append(row)
    st["settings"]["priority"] = keep
    return out


def priority_pick(prows):
    return next((r["start"] for r in prows if r["state"] == "ready" and r["start"]), None)


def forecast(st, rows, prows, n=40, now=None):
    """What autopilot will make, in order (owner, 2026-10-05: "let me see all
    works that are scheduled"). Your Make next entries first, then a
    simulation of round robin (fewest made first, then watchlist order) over
    every series' remaining chapters. Each item says which day it is expected
    on, from today's count and the chapters-a-day limit. Spend can make it
    later than that; the list says so rather than guessing money."""
    per_day = max(1, int(st["settings"].get("per_day") or 1))
    done_today = today_count(st["ledger"], now)
    out = []
    for r in prows:
        if r["state"] in ("ready", "waiting"):
            out.append({"series_id": r["series_id"], "title": r["title"], "chapter": r["chapter"],
                        "from": "make_next", "state": r["state"], "reason": r["reason"]})
    pinned = {(r["series_id"], str(r["chapter"])) for r in prows}
    live = [dict(r, remaining=[c for c in (r["remaining"][1:] if r["state"] == "running" else r["remaining"])
                               if (r["series_id"], str(c)) not in pinned],
                 made=r["made_by_autopilot"]) for r in rows if r["state"] in ("ready", "running")]
    picked_from = {}
    for o in out:                      # Make next picks count for round robin fairness too
        picked_from[o["series_id"]] = picked_from.get(o["series_id"], 0) + 1
    for r in live:
        r["made"] += picked_from.get(r["series_id"], 0)
    while len(out) < n:
        ready = [r for r in live if r["remaining"]]
        if not ready:
            break
        r = min(ready, key=lambda x: (x["made"], x["order"]))
        out.append({"series_id": r["series_id"], "title": r["title"], "chapter": r["remaining"].pop(0),
                    "from": "round_robin", "state": "ready", "reason": ""})
        r["made"] += 1
    slot = done_today
    for o in out:
        if o["state"] == "ready":
            o["day"] = slot // per_day          # 0 = today, 1 = tomorrow, …
            slot += 1
        else:
            o["day"] = None
    return out[:n]


def pick(rows):
    """Round robin: fewest made by autopilot first, then watchlist order."""
    ready = [r for r in rows if r["state"] == "ready" and r["next"] is not None]
    if not ready:
        return None
    return min(ready, key=lambda r: (r["made_by_autopilot"], r["order"]))


# ------------------------------------------------------------------ decision
def decide(st, rows, *, queue_busy, spent, cap, now=None, ap_spent=0.0, prio=None):
    """(waiting_reason or None, pick or None). No side effects. `prio`: the
    first ready Make next entry, which goes before round robin."""
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
    p = prio or pick(rows)
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
        view = deps["view"]()
        live = deps["live_projects"]()
        rows = candidates(st, view, deps["canon"], live, now)
        prows = priority_rows(st, view, deps["canon"], live, now)
        save(root, st)                              # start_from for new series, pruned Next up
        spent, cap = deps["spend"]()
        reason, p = decide(st, rows, queue_busy=deps["queue_busy"](), spent=spent, cap=cap, now=now,
                           ap_spent=deps.get("ap_spend", lambda: 0.0)(), prio=priority_pick(prows))
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
    runtime["last_result"] = f"started {p['title']} ch.{p['next']}" + (" (your scheduled list)" if p.get("priority") else "")
    audit("chapter_queued", **started, why="your scheduled list" if p.get("priority") else "round robin")
    return started


def run_now(root, deps, series_id, chapter, now=None):
    """Owner asks for ONE specific chapter now ("make ch.44 of Murim now").

    Goes through autopilot's own path — same pipeline, Flex tier, ledger
    entry, counted in today's chapters and spend — and joins the normal
    one-at-a-time line. Skips the daily chapter LIMIT (it is an explicit
    request) but never the money: autopilot's budget and the site cap still
    apply. Any chapter the source lists may be asked for, inside or outside
    the start window; one already made or already ingesting is refused."""
    now = time.time() if now is None else now
    chapter = norm_chapter(chapter)
    with _lock:
        st = load(root)
        series = next((s for s in deps["view"]() if s["id"] == series_id), None)
        if series is None:
            raise ValueError(f"no watchlist series '{series_id}'")
        rows = candidates(st, [series], deps["canon"], deps["live_projects"](), now)
        row = rows[0]
        if row["mirror"] is None:
            raise ValueError(row["reason"] or "no readable source for this series")
        best = next(m for m in series["mirrors"] if m["series_key"] == series["best_mirror"])
        listed = {norm_chapter(c) for c in best.get("chapters") or []}
        if chapter not in listed:
            raise ValueError(f"ch.{chapter} is not listed on the source "
                             f"(latest there is ch.{best.get('latest')})")
        key = ledger_key(deps["canon"](best["series_key"]), chapter)
        e = st["ledger"].get(key)
        if chapter in {norm_chapter(c) for c in series.get("ingested") or []} or \
                (e and e.get("status") in MADE):
            raise ValueError(f"ch.{chapter} is already made or in the line")
        spent, cap = deps["spend"]()
        ap_spent, est = deps.get("ap_spend", lambda: 0.0)(), estimate(st["ledger"])
        budget = float(st["settings"].get("budget_usd") or 0)
        if ap_spent + est > budget:
            raise ValueError(f"autopilot budget used for today (${ap_spent:.2f} of ${budget:.2f})")
        if cap and spent + est > cap:
            raise ValueError(f"site spend cap reached for today (${spent:.2f} of ${cap:.2f})")
        url = deps["chapter_url"](row, chapter)
        pid = deps["project_id"](url)
        if pid in deps["live_projects"]():
            raise ValueError(f"ch.{chapter} is already ingesting")
        job = deps["enqueue"](url, st["settings"].get("engine", "gemini"))
        record_queued(root, key, series_id=series_id, chapter=chapter, project=pid,
                      job=job, source="autopilot", url=url)
        save_req = load(root)
        save_req["ledger"][key]["requested"] = True       # made on request, not by the picker
        save(root, save_req)
    started = {"series": series["title"], "chapter": chapter, "job": job, "project": pid,
               "at": now, "requested": True}
    runtime["last_started"] = started
    audit("chapter_requested", **started)
    return started


def status(root, deps, now=None):
    """Rule 40 for the card: state, what happens next, when it last ran, undo."""
    now = time.time() if now is None else now
    with _lock:
        st = load(root)
        view = deps["view"]()
        live = deps["live_projects"]()
        rows = candidates(st, view, deps["canon"], live, now)
        prows = priority_rows(st, view, deps["canon"], live, now)
        save(root, st)
    spent, cap = deps["spend"]()
    ap_spent = deps.get("ap_spend", lambda: 0.0)()
    reason, p = decide(st, rows, queue_busy=deps["queue_busy"](), spent=spent, cap=cap, now=now,
                       ap_spent=ap_spent, prio=priority_pick(prows))
    nxt = p or ((priority_pick(prows) or pick(rows)) if st["settings"].get("enabled") else None)
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
        "next": {"series": nxt["title"], "chapter": nxt["next"], "series_id": nxt["series_id"],
                 "from_next_up": bool(nxt.get("priority"))} if nxt else None,
        "priority": [{k: v for k, v in r.items() if k != "start"} for r in prows],
        "forecast": forecast(st, rows, prows, now=now),
        "last_tick": runtime["last_tick"], "last_result": runtime["last_result"],
        "last_started": runtime["last_started"], "last_refresh": runtime["last_refresh"] or None,
        "tick_seconds": TICK_SECONDS,
        "series": rows, "recent": recent, "undo": UNDO,
    }
