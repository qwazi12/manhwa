"""New-chapter monitor (owner, 2026-11-18: "set up a monitor for new chapters
that runs constantly and is consistent").

Before this the sources were re-read every 6 hours from inside the autopilot
tick. The 6 hours were counted from a value kept in memory (every restart
reset it), nothing was written down about a pass, and no line said that a
chapter had appeared or that a series could no longer be read.

Now:
  - its own thread (server.py), on a fixed interval counted on the clock from
    the start of the last pass, saved to disk: a restart neither skips a pass
    nor runs one early;
  - one record per pass and per series (when, which source answered, the
    latest chapter, how many checks in a row failed);
  - an Activity line for every new chapter, for a series that could not be
    read STUCK_AFTER times running, and when it can be read again;
  - the scheduler (another thread) restarts the monitor if it stops and says
    so; the status says when the next pass is due and whether it is late.
Reading a series page is free. No model call is made here.
"""
import json
import os
import threading
import time

STATE_NAME = "_monitor.json"
DEFAULTS = {"enabled": True, "every_minutes": 30}
MIN_EVERY, MAX_EVERY = 10, 720
STUCK_AFTER = 3           # checks in a row a series must fail before it is reported
GAP_SECONDS = 2.0         # pause between two series pages (polite to the sources)
MAX_NEW = 10              # more new chapters than this at once is another numbering, not a release
FOUND_KEEP = 200          # new-chapter records kept
LATE_AFTER = 2.0          # a pass this many intervals overdue is reported as late
PASS_MAX_S = 20 * 60      # a pass "running" longer than this is taken for dead

runtime = {"running": False, "pass_started": None, "beat": 0.0, "thread": None}
_lock = threading.RLock()


def _path(root):
    return os.path.join(root, STATE_NAME)


def load(root):
    try:
        with open(_path(root), encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            st = {}
    except (OSError, ValueError):
        st = {}
    st["settings"] = {**DEFAULTS, **(st.get("settings") or {})}
    st.setdefault("series", {})
    st.setdefault("found", [])
    st.setdefault("last_pass", None)
    return st


def save(root, st):
    os.makedirs(root, exist_ok=True)
    tmp = _path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, ensure_ascii=False)
    os.replace(tmp, _path(root))


def update_settings(root, enabled=None, every_minutes=None):
    with _lock:
        st = load(root)
        if enabled is not None:
            st["settings"]["enabled"] = bool(enabled)
        if every_minutes is not None:
            try:
                n = int(every_minutes)
            except (TypeError, ValueError):
                raise ValueError("every_minutes must be a whole number")
            if not MIN_EVERY <= n <= MAX_EVERY:
                raise ValueError(f"every_minutes must be between {MIN_EVERY} and {MAX_EVERY}")
            st["settings"]["every_minutes"] = n
        save(root, st)
        return st["settings"]


def next_due(st):
    """Clock time of the next pass: one interval after the START of the last
    one (so a slow pass does not push the schedule later and later)."""
    lp = st.get("last_pass") or {}
    # "slot" is the time the last pass was DUE; counting from it (not from the
    # few seconds later at which the thread noticed) keeps the passes on a
    # fixed grid instead of sliding a little later every time.
    last = lp.get("slot") or lp.get("started")
    return (float(last) + st["settings"]["every_minutes"] * 60) if last else 0.0


def due(st, now=None):
    now = time.time() if now is None else now
    return bool(st["settings"].get("enabled")) and now >= next_due(st)


def _chapters(mirror):
    return [str(c) for c in (mirror or {}).get("chapters") or []]


def _main(series):
    key = series.get("best_mirror")
    return next((m for m in series.get("mirrors") or [] if m.get("series_key") == key), None)


def run_pass(root, series_view, refresh, now=None, sleep=time.sleep, say=None, gap=GAP_SECONDS):
    """Read every followed series once. `series_view()` -> the watchlist view's
    series; `refresh(series_id)` -> the main source's record after the read
    (watchlist.refresh_series). `say(kind, text, level)` gets one line per new
    chapter, per series that stopped answering and per recovery. Returns the
    pass record. Never raises for one series' failure."""
    clock = now or time.time
    say = say or (lambda kind, text, level="info": None)
    started = clock()
    with _lock:
        if runtime["running"] and started - (runtime["pass_started"] or 0) < PASS_MAX_S:
            return None                                  # one pass at a time
        runtime.update(running=True, pass_started=started)
        st = load(root)
        was_due = next_due(st)
        # on time (less than one interval after it was due): keep the grid;
        # first pass, a manual "check now" before it was due, or a long gap: a new grid starts here
        slot = was_due if was_due and 0 <= started - was_due < st["settings"]["every_minutes"] * 60 else started
        st["last_pass"] = {**(st.get("last_pass") or {}), "started": started, "slot": slot, "running": True}
        save(root, st)                                   # the schedule moves on even if this pass dies
    checked = failed = 0
    new_total, results, rows, listed = [], {}, [], False
    try:
        rows = [s for s in series_view() if s.get("best_mirror")]
        listed = True
        for i, s in enumerate(rows):
            runtime["beat"] = clock()
            sid, title = s["id"], s.get("title") or s["id"]
            main = _main(s)
            before = set(_chapters(main))
            first_time = not before
            try:
                m = refresh(sid) or {}
                err = None
            except Exception as e:          # noqa — one series must not stop the pass
                m, err = {}, f"{type(e).__name__}: {e}"[:300]
            bk = m.get("backup") or {}
            via_backup = bool(bk) and float(bk.get("at") or 0) >= started - 1 and not bk.get("skipped")
            ok = err is None and (m.get("status") == "ok" or via_backup)
            source = (bk.get("label") if via_backup and m.get("status") != "ok" else m.get("label")) or ""
            new = [] if first_time or not ok else sorted(set(_chapters(m)) - before, key=lambda c: (len(c), c))
            renumbered = len(new) > MAX_NEW
            results[sid] = {"title": title, "ok": ok, "source": source, "latest": m.get("latest"),
                            "error": None if ok else (err or m.get("error") or bk.get("skipped")
                                                      or "the source could not be read"),
                            "new": [] if renumbered else new, "renumbered": renumbered}
            checked += 1
            failed += 0 if ok else 1
            if new and not renumbered:
                new_total.append((sid, title, source, new))
            if i + 1 < len(rows) and gap:
                sleep(gap)
    finally:
        ended = clock()
        with _lock:
            st = load(root)
            for sid, r in results.items():
                rec = st["series"].get(sid) or {"fails": 0}
                rec.update(title=r["title"], checked_at=ended, ok=r["ok"], source=r["source"],
                           latest=r["latest"], error=r["error"])
                if r["ok"]:
                    if rec.get("stuck"):
                        say("tracker", f"{r['title']}: can be read again"
                                       + (f" (through {r['source']})" if r["source"] else ""), "ok")
                    rec.update(fails=0, stuck=False, last_ok=ended)
                else:
                    rec["fails"] = int(rec.get("fails") or 0) + 1
                    if rec["fails"] >= STUCK_AFTER and not rec.get("stuck"):
                        rec["stuck"] = True
                        say("tracker", f"{r['title']}: no source could be read {rec['fails']} checks in a row "
                                       f"({r['error']}). New chapters of it cannot be seen until one answers.", "error")
                if r["renumbered"]:
                    say("tracker", f"{r['title']}: the chapter list changed by more than {MAX_NEW} at once; "
                                   "taken for a renumbering, not for new chapters", "warn")
                for c in r["new"]:
                    st["found"].append({"series_id": sid, "title": r["title"], "chapter": c,
                                        "source": r["source"], "at": ended})
                if r["new"]:
                    rec["last_new"] = {"chapters": r["new"], "at": ended}
                st["series"][sid] = rec
            # a series no longer followed leaves the record
            if listed:
                followed = {s["id"] for s in rows}
                st["series"] = {k: v for k, v in st["series"].items() if k in followed}
            st["found"] = st["found"][-FOUND_KEEP:]
            pass_rec = {"started": started, "slot": slot, "ended": ended, "running": False, "checked": checked,
                        "failed": failed, "new": sum(len(n[3]) for n in new_total)}
            st["last_pass"] = pass_rec
            st["passes"] = int(st.get("passes") or 0) + 1
            save(root, st)
            runtime.update(running=False, beat=ended)
        for sid, title, source, new in new_total:
            say("tracker", f"{title}: new chapter{'s' if len(new) > 1 else ''} {', '.join(new)}"
                           + (f" (listed by {source})" if source else ""), "ok")
    return pass_rec


def status(root, made=None, now=None):
    """What the monitor is doing: the state, what happens next, when it last
    ran, how to stop it. `made(series_id, chapter)` -> True when that chapter
    already exists in the studio (marks found chapters still waiting)."""
    now = time.time() if now is None else now
    st = load(root)
    s = st["settings"]
    nd = next_due(st)
    every = s["every_minutes"] * 60
    lp = st.get("last_pass")
    running = bool(runtime["running"]) and now - (runtime["pass_started"] or 0) < PASS_MAX_S
    late = bool(s["enabled"]) and bool(lp) and not running and now > nd + (LATE_AFTER - 1) * every
    found = []
    for f in reversed(st["found"][-60:]):
        f = dict(f)
        if made is not None:
            try:
                f["made"] = bool(made(f["series_id"], f["chapter"]))
            except Exception:  # noqa
                f["made"] = None
        found.append(f)
    series = sorted(({"id": k, **v} for k, v in st["series"].items()),
                    key=lambda r: (r.get("ok", True), (r.get("title") or "").lower()))
    return {"enabled": bool(s["enabled"]), "every_minutes": s["every_minutes"], "running": running,
            "last_pass": lp, "passes": int(st.get("passes") or 0),
            "next_due": nd if s["enabled"] else None, "late": late,
            "thread_alive": bool(runtime["thread"] and runtime["thread"].is_alive()),
            "series_followed": len(series), "stuck": [r for r in series if r.get("stuck")],
            "failing": [r for r in series if not r.get("ok", True)],
            "series": series, "found": found,
            "waiting": [f for f in found if f.get("made") is False],
            "undo": "POST /api/monitor {\"enabled\": false} stops the checks; Home shows the same switch."}


def loop(root_fn, series_view, refresh, say, stop=None, tick=30.0, sleep=time.sleep, first_wait=0.0):
    """The monitor thread: a pass whenever one is due. Survives any error."""
    if first_wait:
        sleep(first_wait)                    # let the server finish booting
    while not (stop and stop.is_set()):
        runtime["beat"] = time.time()
        try:
            root = root_fn()
            if due(load(root)):
                run_pass(root, series_view, refresh, say=say)
        except Exception as e:  # noqa — the monitor must outlive a bad pass
            print(f"[monitor] pass failed: {type(e).__name__}: {e}", flush=True)
            try:
                say("tracker", f"new-chapter check failed: {type(e).__name__}: {str(e)[:160]}", "error")
            except Exception:
                pass
        sleep(tick)


def ensure_running(root_fn, series_view, refresh, say, first_wait=0.0):
    """Start the monitor thread if it is not alive. Returns True when it had
    to be (re)started. Called at boot and from the scheduler as a watchdog."""
    with _lock:
        t = runtime.get("thread")
        if t is not None and t.is_alive():
            return False
        again = t is not None
        t = threading.Thread(target=loop, args=(root_fn, series_view, refresh, say),
                             kwargs={"first_wait": first_wait}, daemon=True, name="chapter-monitor")
        runtime["thread"] = t
        t.start()
    return again
