"""Owner, 2026-11-18: "set up a monitor for new chapters that runs constantly
and is consistent". Before: sources were re-read every 6 hours from inside the
autopilot tick, counted from a value kept in memory; nothing was recorded and
nothing said when a chapter appeared or a series stopped answering.
No network. Run: python3 -m pytest test_chapter_monitor.py -q"""
import os
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
import chapter_monitor as cm  # noqa: E402


class Sources:
    """Two followed series whose chapter lists and failures the test sets."""
    def __init__(self):
        self.lists = {"orv": ["309", "310", "311"], "fog": ["45", "46"]}
        self.fail = {}                      # series id -> error text
        self.backup = {}                    # series id -> chapters only the backup lists
        self.read = []
        self.clock = 1_000_000.0

    def now(self):
        return self.clock

    def view(self):
        return [{"id": sid, "title": sid.upper(), "best_mirror": sid + "-main",
                 "mirrors": [{"series_key": sid + "-main", "chapters": list(self._known.get(sid, []))}]}
                for sid in self.lists] + [{"id": "nosource", "title": "No source", "best_mirror": None, "mirrors": []}]

    _known = {}

    def refresh(self, sid):
        self.read.append(sid)
        known = list(self._known.get(sid, []))
        if sid in self.fail:
            if sid in self.backup:
                added = [c for c in self.backup[sid] if c not in known]
                self._known = {**self._known, sid: known + added}
                return {"status": "error", "error": self.fail[sid], "label": "Asura", "chapters": known + added,
                        "latest": (known + added)[-1],
                        "backup": {"label": "MangaGeko", "at": self.clock, "added": added, "skipped": None}}
            return {"status": "error", "error": self.fail[sid], "label": "Asura", "chapters": known,
                    "latest": known[-1] if known else None}
        self._known = {**self._known, sid: list(self.lists[sid])}
        return {"status": "ok", "label": "Asura", "chapters": list(self.lists[sid]), "latest": self.lists[sid][-1]}


@pytest.fixture
def mon(tmp_path):
    cm.runtime.update(running=False, pass_started=None, beat=0.0, thread=None)
    src = Sources()
    said = []

    def go(minutes_later=0):
        src.clock += minutes_later * 60
        return cm.run_pass(str(tmp_path), src.view, src.refresh, now=src.now, sleep=lambda s: None,
                           say=lambda kind, text, level="info": said.append((level, text)))
    return str(tmp_path), src, said, go


def test_the_first_pass_records_every_series_and_calls_nothing_new(mon):
    root, src, said, go = mon
    rec = go()
    assert (rec["checked"], rec["failed"], rec["new"]) == (2, 0, 0)
    assert src.read == ["orv", "fog"] and said == []          # a series without a source is not read
    st = cm.status(root, now=src.clock)
    assert st["series_followed"] == 2 and st["found"] == [] and st["failing"] == []
    assert {s["id"]: s["latest"] for s in st["series"]} == {"orv": "311", "fog": "46"}


def test_a_new_chapter_is_recorded_and_announced_once(mon):
    root, src, said, go = mon
    go()
    src.lists["orv"].append("312")
    rec = go(30)
    assert rec["new"] == 1 and said == [("ok", "ORV: new chapter 312 (listed by Asura)")]
    st = cm.status(root, made=lambda sid, ch: False, now=src.clock)
    assert [(f["series_id"], f["chapter"], f["made"]) for f in st["found"]] == [("orv", "312", False)]
    assert [f["chapter"] for f in st["waiting"]] == ["312"]
    said.clear()
    assert go(30)["new"] == 0 and said == []                  # not announced again
    assert cm.status(root, made=lambda sid, ch: True, now=src.clock)["waiting"] == []


def test_a_chapter_only_the_backup_source_lists_counts_as_read_and_as_new(mon):
    root, src, said, go = mon
    go()
    src.fail["orv"] = "HTTP Error 403: Forbidden"
    src.backup["orv"] = ["312"]
    rec = go(30)
    assert (rec["failed"], rec["new"]) == (0, 1)
    assert said == [("ok", "ORV: new chapter 312 (listed by MangaGeko)")]


def test_a_series_that_stops_answering_is_reported_once_and_again_when_it_is_back(mon):
    root, src, said, go = mon
    go()
    src.fail["fog"] = "HTTP Error 403: Forbidden"
    go(30)
    go(30)
    assert said == []                                          # two failures: not yet
    go(30)
    assert len(said) == 1 and said[0][0] == "error" and "FOG" in said[0][1] and "3 checks in a row" in said[0][1]
    go(30)
    assert len(said) == 1                                      # said once, not every pass
    st = cm.status(root, now=src.clock)
    assert [s["id"] for s in st["stuck"]] == ["fog"] and st["stuck"][0]["fails"] == 4
    del src.fail["fog"]
    go(30)
    assert said[-1] == ("ok", "FOG: can be read again (through Asura)")
    assert cm.status(root, now=src.clock)["stuck"] == []


def test_one_series_failing_does_not_stop_the_pass(mon):
    root, src, said, go = mon
    real = src.refresh

    def refresh(sid):
        if sid == "orv":
            src.read.append(sid)
            raise OSError("connection reset")
        return real(sid)
    rec = cm.run_pass(root, src.view, refresh, now=src.now, sleep=lambda s: None)
    assert (rec["checked"], rec["failed"]) == (2, 1) and src.read == ["orv", "fog"]
    st = cm.status(root, now=src.clock)
    assert [s["id"] for s in st["failing"]] == ["orv"] and "connection reset" in st["failing"][0]["error"]


def test_a_changed_numbering_is_not_taken_for_new_chapters(mon):
    root, src, said, go = mon
    go()
    src.lists["orv"] = [str(n) for n in range(400, 400 + cm.MAX_NEW + 1)]
    rec = go(30)
    assert rec["new"] == 0 and said[-1][0] == "warn" and "renumbering" in said[-1][1]
    assert cm.status(root, now=src.clock)["found"] == []


def test_the_schedule_is_counted_on_the_clock_from_the_start_of_the_last_pass(mon):
    root, src, said, go = mon
    assert cm.due(cm.load(root), now=src.clock)                # never ran: due now
    go()
    start = src.clock
    st = cm.load(root)
    assert not cm.due(st, now=start + 29 * 60) and cm.due(st, now=start + 30 * 60)
    assert cm.next_due(st) == start + 30 * 60
    # the record is on disk: a restart (fresh module state) keeps the same schedule
    cm.runtime.update(running=False, pass_started=None, thread=None)
    assert cm.next_due(cm.load(root)) == start + 30 * 60
    assert cm.status(root, now=start + 61 * 60)["late"] is True
    assert cm.status(root, now=start + 31 * 60)["late"] is False


def test_a_pass_that_starts_a_few_seconds_late_does_not_move_the_schedule(mon):
    """Production, 2026-11-18: due 04:08:35Z, started 04:08:42Z (the thread looks every 30 s).
    Counted from the start, every pass would slide a little later."""
    root, src, said, go = mon
    go()
    first = src.clock
    src.clock = first + 30 * 60 + 7                       # noticed 7 s after it was due
    go()
    assert cm.next_due(cm.load(root)) == first + 60 * 60   # still on the half-hour grid
    src.clock = first + 60 * 60 + 29
    go()
    assert cm.next_due(cm.load(root)) == first + 90 * 60
    # after a long gap (the server was down for hours) a new grid starts at the pass
    src.clock = first + 300 * 60 + 5
    go()
    assert cm.next_due(cm.load(root)) == src.clock + 30 * 60


def test_settings_are_bounded_and_off_means_never_due(mon):
    root, src, said, go = mon
    with pytest.raises(ValueError):
        cm.update_settings(root, every_minutes=1)
    assert cm.update_settings(root, every_minutes=15) == {"enabled": True, "every_minutes": 15}
    cm.update_settings(root, enabled=False)
    assert not cm.due(cm.load(root), now=src.clock + 10 ** 6)
    st = cm.status(root, now=src.clock)
    assert st["enabled"] is False and st["next_due"] is None and st["late"] is False and "undo" in st


def test_two_passes_never_run_at_once(mon):
    root, src, said, go = mon
    inside, release = threading.Event(), threading.Event()
    real = src.refresh

    def slow(sid):
        inside.set()
        release.wait(5)
        return real(sid)
    t = threading.Thread(target=cm.run_pass, args=(root, src.view, slow),
                         kwargs={"now": src.now, "sleep": lambda s: None})
    t.start()
    assert inside.wait(5)
    assert cm.run_pass(root, src.view, src.refresh, now=src.now, sleep=lambda s: None) is None
    release.set()
    t.join(5)
    assert cm.runtime["running"] is False


def test_the_watchdog_restarts_a_monitor_that_stopped(mon, monkeypatch):
    root, src, said, go = mon
    ran = threading.Event()
    monkeypatch.setattr(cm, "loop", lambda *a, **k: ran.set())      # a loop that ends at once = a dead thread
    assert cm.ensure_running(lambda: root, src.view, src.refresh, None) is False     # first start
    assert ran.wait(5)
    cm.runtime["thread"].join(5)
    ran.clear()
    assert cm.ensure_running(lambda: root, src.view, src.refresh, None) is True      # it had stopped: restarted
    assert ran.wait(5)
    cm.runtime["thread"].join(5)
    alive = threading.Event()
    monkeypatch.setattr(cm, "loop", lambda *a, **k: alive.wait(5))
    assert cm.ensure_running(lambda: root, src.view, src.refresh, None) is True
    assert cm.ensure_running(lambda: root, src.view, src.refresh, None) is False     # alive: left alone
    alive.set()


def test_the_loop_runs_a_pass_when_due_and_outlives_an_error(mon):
    root, src, said, go = mon
    stop = threading.Event()
    calls = {"n": 0}

    def view():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("watchlist unreadable")
        stop.set()
        return src.view()
    def next_interval(_s):                   # the wait between two looks: the next pass comes due
        if stop.is_set():
            return
        st = cm.load(root)
        st["last_pass"] = None
        cm.save(root, st)
        if calls["n"] > 5:
            stop.set()
    cm.loop(lambda: root, view, src.refresh, lambda kind, text, level="info": said.append((level, text)),
            stop=stop, tick=0, sleep=next_interval)
    assert calls["n"] == 2 and any(lv == "error" and "watchlist unreadable" in tx for lv, tx in said)
    assert cm.load(root)["last_pass"]["checked"] == 2


def test_the_server_routes(tmp_path, monkeypatch):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SHARED_SECRET", "s")
    monkeypatch.setattr(server, "_monitor_root", lambda: str(tmp_path))
    src = Sources()
    monkeypatch.setattr(server, "_monitor_series", src.view)
    monkeypatch.setattr(server, "_monitor_refresh", src.refresh)
    monkeypatch.setattr(server, "_monitor_made", lambda sid, ch: False)
    monkeypatch.setattr(server, "_ev", lambda *a, **k: None)
    monkeypatch.setattr(cm, "GAP_SECONDS", 0)
    cm.runtime.update(running=False, pass_started=None, thread=None)
    c = TestClient(server.app)
    h = {"x-shared-secret": "s"}
    assert c.get("/api/monitor").status_code == 401
    st = c.get("/api/monitor", headers=h).json()
    assert st["enabled"] is True and st["every_minutes"] == 30 and st["last_pass"] is None
    assert c.post("/api/monitor/run", headers=h, json={}).json()["started"] is True
    for _ in range(100):
        if (c.get("/api/monitor", headers=h).json()["last_pass"] or {}).get("ended"):
            break
        time.sleep(0.05)
    st = c.get("/api/monitor", headers=h).json()
    assert st["last_pass"]["checked"] == 2 and st["series_followed"] == 2
    assert c.post("/api/monitor", headers=h, json={"every_minutes": 5}).status_code == 400
    r = c.post("/api/monitor", headers=h, json={"enabled": False, "every_minutes": 60}).json()
    assert r["enabled"] is False and r["every_minutes"] == 60
