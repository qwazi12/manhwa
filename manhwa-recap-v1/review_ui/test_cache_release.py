"""Idle file-cache release (Railway bills the cache as memory). Temp dirs only."""
import os
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import cache_release as cr


@pytest.fixture
def advised(monkeypatch):
    """Record posix_fadvise calls (macOS has no posix_fadvise)."""
    calls = []
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
    monkeypatch.setattr(os, "posix_fadvise",
                        lambda fd, off, n, advice: calls.append((fd, advice)), raising=False)
    return calls


def tree(tmp_path):
    (tmp_path / "data/projects/ch1/exports").mkdir(parents=True)
    (tmp_path / "data/projects/ch1/exports/final.mp4").write_bytes(b"x" * 10)
    (tmp_path / "data/projects/ch1/segments.json").write_text("[]")
    (tmp_path / "data/workspace").mkdir()
    (tmp_path / "data/workspace/clip.mp4").write_bytes(b"y")
    os.symlink(tmp_path / "data/projects", tmp_path / "projects_link")
    return tmp_path


def test_every_file_is_advised_once_even_through_a_symlinked_root(tmp_path, advised):
    t = tree(tmp_path)
    n, errs = cr.release([t / "projects_link", t / "data", t / "data/workspace", t / "missing"])
    assert (n, errs) == (3, 0)
    assert len(advised) == 3 and all(a == 4 for _, a in advised)


def test_files_are_never_modified(tmp_path, advised):
    t = tree(tmp_path)
    f = t / "data/projects/ch1/exports/final.mp4"
    before = (f.read_bytes(), f.stat().st_mtime)
    cr.release([t / "data"])
    assert (f.read_bytes(), f.stat().st_mtime) == before


def test_waits_while_work_runs_or_cache_is_small(tmp_path, advised, monkeypatch):
    t = tree(tmp_path)
    logs = []
    big = {"current": 3e9, "anon": 1e8, "file": 2.9e9}
    monkeypatch.setattr(cr, "cgroup_memory", lambda: dict(big))
    assert cr.run_once([t], lambda: True, logs.append)["reason"] == "work running"
    def boom():
        raise RuntimeError("queue unreadable")
    assert cr.run_once([t], boom, logs.append)["acted"] is False
    monkeypatch.setattr(cr, "cgroup_memory", lambda: {"current": 2e8, "anon": 1e8, "file": 1e8})
    assert cr.run_once([t], lambda: False, logs.append)["reason"] == "little file cache"
    assert advised == [] and logs == []


def test_acts_when_idle_and_logs_the_saving(tmp_path, advised, monkeypatch):
    t = tree(tmp_path)
    states = iter([{"current": 3e9, "anon": 1e8, "file": 2.9e9},
                   {"current": 2e8, "anon": 1e8, "file": 1e8}])
    monkeypatch.setattr(cr, "cgroup_memory", lambda: next(states))
    logs = []
    out = cr.run_once([t / "data"], lambda: False, lambda *a, **k: logs.append(a[0]))
    assert out["acted"] and out["files"] == 3
    assert "3.00GB -> 0.20GB" in logs[0]


def test_switch_off_and_unsupported_platforms_start_nothing(monkeypatch, advised):
    monkeypatch.setenv("CACHE_RELEASE", "off")
    assert cr.start(["/nowhere"], lambda: False) is None
    monkeypatch.delenv("CACHE_RELEASE")
    monkeypatch.delattr(os, "posix_fadvise")
    assert cr.start(["/nowhere"], lambda: False) is None


@pytest.mark.skipif(not hasattr(os, "posix_fadvise"), reason="Linux only")
def test_real_kernel_call_on_linux(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(os.urandom(1 << 20))
    assert cr.release([tmp_path]) == (1, 0)


def test_idle_check_ignores_paused_and_held_work(monkeypatch):
    import server
    monkeypatch.setattr(server, "INGEST", {"a": {"status": "budget_paused"}, "b": {"status": "cancelled"}})
    monkeypatch.setattr(server, "JOBS", {"c": {"status": "paused"}, "d": {"status": "done"}})
    monkeypatch.setattr(server._rq, "load", lambda root: {"items": [{"status": "waiting"}, {"status": "done"}]})
    assert server._work_running() is False
    monkeypatch.setattr(server, "JOBS", {"c": {"status": "running"}})
    assert server._work_running() is True
    monkeypatch.setattr(server, "JOBS", {})
    monkeypatch.setattr(server._rq, "load", lambda root: {"items": [{"status": "rendering"}]})
    assert server._work_running() is True
