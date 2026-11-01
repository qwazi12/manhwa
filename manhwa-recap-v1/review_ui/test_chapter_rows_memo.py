"""server._chapter_rows: one build shared by concurrent readers for a few
seconds; any change request makes the next read rebuild (owner, 2026-11-01:
pages slow because every poll re-walked every chapter folder)."""
import threading
import time

from fastapi.testclient import TestClient

import server as srv


def _counting(monkeypatch, delay=0.0):
    calls = []

    def build():
        calls.append(1)
        time.sleep(delay)
        return [{"id": "a", "status": {"key": "failed"}}]
    monkeypatch.setattr(srv, "_build_chapter_rows", build)
    monkeypatch.setattr(srv, "_ROWS_TTL", 30.0)
    srv._ROWS_MEMO.update(at=0.0, gen=-1, rows=None)
    return calls


def test_reads_within_ttl_share_one_build(monkeypatch):
    calls = _counting(monkeypatch)
    assert srv._chapter_rows() == srv._chapter_rows()
    assert len(calls) == 1


def test_concurrent_reads_wait_for_the_build_in_progress(monkeypatch):
    calls = _counting(monkeypatch, delay=0.2)
    out = []
    ts = [threading.Thread(target=lambda: out.append(srv._chapter_rows())) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(out) == 5 and len(calls) == 1


def test_callers_get_their_own_copy(monkeypatch):
    _counting(monkeypatch)
    srv._chapter_rows()[0]["status"]["key"] = "changed"
    assert srv._chapter_rows()[0]["status"]["key"] == "failed"


def test_expires_after_ttl(monkeypatch):
    calls = _counting(monkeypatch)
    srv._chapter_rows()
    srv._ROWS_MEMO["at"] -= 31
    srv._chapter_rows()
    assert len(calls) == 2


def test_a_change_request_makes_the_next_read_rebuild(monkeypatch):
    calls = _counting(monkeypatch)
    monkeypatch.setenv("SHARED_SECRET", "")
    c = TestClient(srv.app)
    srv._chapter_rows()
    c.get("/health")
    srv._chapter_rows()
    assert len(calls) == 1                     # a read changes nothing
    c.post("/api/does-not-exist", json={})
    srv._chapter_rows()
    assert len(calls) == 2
