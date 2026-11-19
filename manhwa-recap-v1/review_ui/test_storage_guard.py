"""The disk guard (owner, 2026-11-19: "Why is the disk filling up? It should
free up the space."). Live: 0 bytes free while the Deleted bin held 5.4 GB and
merged videos set aside by unmerge (_unmerged_*) held 4 GB, never cleaned."""
import os
import time
from collections import namedtuple

import pytest

import ingest
import server

DU = namedtuple("DU", "total used free")


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "_ev", lambda *a, **k: None)
    monkeypatch.setattr(server, "_retention_queued", lambda: [])
    return tmp_path


def _disk(monkeypatch, free_gb):
    """free space that grows by 1 GB for every bin item removed."""
    import shutil
    state = {"free": free_gb}
    real = shutil.rmtree

    def rm(p, *a, **k):
        state["free"] += 1
        return real(p, *a, **k)
    monkeypatch.setattr(shutil, "rmtree", rm)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: DU(46e9, 46e9 - state["free"] * 1e9, state["free"] * 1e9))
    return state


def _binitem(lib, name, age_days):
    p = lib / "_trash" / name
    p.mkdir(parents=True)
    (p / "f.bin").write_bytes(b"x")
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    return p


def test_old_unmerged_copy_goes_to_the_bin(lib, monkeypatch):
    _disk(monkeypatch, 20)
    old = int(time.time()) - 7 * 3600
    new = int(time.time()) - 600
    (lib / f"_unmerged_hound_ch181-189_{old}").mkdir()
    (lib / f"_unmerged_hound_ch181-190_{new}").mkdir()
    out = server._storage_guard()
    assert out["binned"] == [f"hound_ch181-189__{old}"]
    assert (lib / "_trash" / f"hound_ch181-189__{old}").is_dir()       # restorable from the Deleted tab
    assert (lib / f"_unmerged_hound_ch181-190_{new}").is_dir()         # a fresh one waits
    assert out["purged"] == []                                         # disk not low: nothing deleted


def test_low_disk_empties_the_bin_oldest_first_until_enough_is_free(lib, monkeypatch):
    st = _disk(monkeypatch, server.LOW_DISK_FREE_GB - 2.5)
    _binitem(lib, "a__1", 9)
    _binitem(lib, "b__2", 5)
    _binitem(lib, "c__3", 1)
    _binitem(lib, "d__4", 0.5)
    out = server._storage_guard()
    assert out["purged"] == ["a__1", "b__2", "c__3"]                   # stops once free >= the floor
    assert (lib / "_trash" / "d__4").is_dir()
    assert st["free"] >= server.LOW_DISK_FREE_GB


def test_nothing_is_deleted_while_there_is_room(lib, monkeypatch):
    _disk(monkeypatch, server.LOW_DISK_FREE_GB + 5)
    _binitem(lib, "a__1", 2)
    out = server._storage_guard()
    assert out["purged"] == [] and (lib / "_trash" / "a__1").is_dir()
    assert out["retention"] is None


def test_still_low_after_the_bin_clears_old_work_folders(lib, monkeypatch):
    _disk(monkeypatch, 0.0)
    calls = []

    def fake_apply(root, cats, **k):
        calls.append(sorted(cats))
        assert k["environ"]["RETENTION_WORK_DAYS"] == str(server.LOW_DISK_WORK_DAYS)   # shorter window while low
        d = os.path.join(root, "_trash", "retention-x")
        os.makedirs(d, exist_ok=True)
        return {"items": [{"path": "_splitlab/old"}], "freed_bytes": 10, "skipped": []}
    import retention
    monkeypatch.setattr(retention, "apply", fake_apply)
    out = server._storage_guard()
    assert calls == [sorted(server.GUARD_CATEGORIES)]
    assert "tts_cache" not in server.GUARD_CATEGORIES and "trash" not in server.GUARD_CATEGORIES
    assert "retention-x" in out["purged"]
    assert server._STORAGE_GUARD["last"] is out
