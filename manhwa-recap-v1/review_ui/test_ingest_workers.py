"""Chapters made at the same time (INGEST_WORKERS, 2026-11-14): bounded by the
setting, never the same chapter twice at once, and the stages that share
module-level results run one chapter at a time. One worker (the default) is
the old one-at-a-time behaviour."""
import os
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest  # noqa: E402
import server  # noqa: E402


@pytest.fixture
def queue(monkeypatch):
    seen = {"now": 0, "peak": 0, "order": [], "overlap": 0, "active": set()}
    lock = threading.Lock()

    def run(job_id, url, fresh, engine="gemini", variant="", direct=None):
        with lock:
            seen["now"] += 1
            seen["peak"] = max(seen["peak"], seen["now"])
            if url in seen["active"]:
                seen["overlap"] += 1
            seen["active"].add(url)
            seen["order"].append(job_id)
        time.sleep(0.05)
        if "boom" in url:
            with lock:
                seen["now"] -= 1
                seen["active"].discard(url)
            raise RuntimeError("worker crashed")
        with lock:
            seen["now"] -= 1
            seen["active"].discard(url)
    monkeypatch.setattr(server, "_run_ingest_job", run)
    monkeypatch.setattr(server, "_persist_ingest", lambda j: None)
    monkeypatch.setattr(server, "_load_ingest", lambda j: None)
    monkeypatch.setattr(server, "_ev", lambda *a, **k: None)
    monkeypatch.setattr(server, "_queue_key", lambda url, variant="": url + "|" + variant)
    server._QUEUE.clear()
    server._QUEUE_BUSY.clear()

    def drain():
        for _ in range(400):
            with server._QUEUE_LOCK:
                if not server._QUEUE and server._QUEUE_ACTIVE == 0:
                    return
            time.sleep(0.01)
        raise AssertionError("queue never drained")
    return seen, drain


def _add(url):
    return server._enqueue_ingest(url)


def test_one_worker_by_default_runs_chapters_one_at_a_time(queue, monkeypatch):
    seen, drain = queue
    monkeypatch.delenv("INGEST_WORKERS", raising=False)
    ids = [_add(f"https://x/ch/{i}") for i in range(4)]
    drain()
    assert seen["peak"] == 1 and seen["order"] == ids


def test_workers_are_bounded_by_the_setting(queue, monkeypatch):
    seen, drain = queue
    monkeypatch.setenv("INGEST_WORKERS", "3")
    for i in range(9):
        _add(f"https://x/ch/{i}")
    drain()
    assert seen["peak"] == 3 and len(seen["order"]) == 9
    assert server._QUEUE_ACTIVE == 0 and not server._QUEUE_RUNNING and not server._QUEUE_BUSY


def test_same_chapter_is_never_made_by_two_workers_at_once(queue, monkeypatch):
    seen, drain = queue
    monkeypatch.setenv("INGEST_WORKERS", "4")
    for _ in range(3):
        _add("https://x/ch/7")                    # three jobs for one chapter (a retry, a resume …)
    _add("https://x/ch/8")
    drain()
    assert seen["overlap"] == 0 and len(seen["order"]) == 4 and seen["peak"] == 2


def test_a_crashing_worker_does_not_stop_the_others_or_leak_its_slot(queue, monkeypatch):
    seen, drain = queue
    monkeypatch.setenv("INGEST_WORKERS", "2")
    _add("https://x/boom")
    for i in range(4):
        _add(f"https://x/ch/{i}")
    drain()
    assert len(seen["order"]) == 5 and server._QUEUE_ACTIVE == 0 and not server._QUEUE_BUSY


def test_setting_is_clamped(monkeypatch):
    for raw, want in (("0", 1), ("-3", 1), ("junk", 1), ("2", 2), ("99", 8)):
        monkeypatch.setenv("INGEST_WORKERS", raw)
        assert server._ingest_workers() == want


def test_script_and_framing_stages_take_one_chapter_at_a_time():
    inside, peak, lock = [0], [0], threading.Lock()

    @ingest._releases_stage_locks
    def chapter(fail=False):
        ingest._stage_enter("script")
        with lock:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
        time.sleep(0.02)
        with lock:
            inside[0] -= 1
        if fail:
            raise RuntimeError("the script step failed")      # the lock must still come back
        ingest._stage_exit("script")

    def go(i):
        try:
            chapter(fail=(i % 2 == 0))
        except RuntimeError:
            pass
    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join(5) for t in ts]
    assert peak[0] == 1 and not any(t.is_alive() for t in ts)
    assert ingest._STAGE_LOCKS["script"].acquire(timeout=1)       # nobody left it locked
    ingest._STAGE_LOCKS["script"].release()
