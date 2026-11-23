"""Owner, 2026-11-23, end to end on a temp library: a series with nothing
posted has an approved, queued 1-2; chapter 3 renders → 1-3 is built beside it
(1-2 stays queued); approving 1-3 puts it in 1-2's place and bins 1-2."""
import json
import os
import time

import pytest

import ingest
import publish_queue as pq
import range_compile as rc
import server
import yt_api


def _wait(root, pid):
    for _ in range(200):
        try:
            m = json.load(open(os.path.join(root, pid, "project.json")))
        except OSError:
            m = {}
        if (m.get("build") or {}).get("status") in ("done", "error"):
            for _ in range(100):                       # the build thread lets go of the range last
                if pid not in server._RANGE_BUILDING:
                    return m
                time.sleep(0.02)
            return m
        time.sleep(0.05)
    raise AssertionError(f"{pid} never finished")


def _chapter(root, sid, n):
    d = os.path.join(root, f"{sid}_{n}")
    os.makedirs(os.path.join(d, "exports"))
    open(os.path.join(d, "exports", "final.mp4"), "wb").write(b"v")
    json.dump({"id": f"{sid}_{n}", "series": sid.title(), "series_id": sid, "chapter": str(n)},
              open(os.path.join(d, "project.json"), "w"))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "_yt_root", lambda: os.path.join(root, "_yt"))
    monkeypatch.setattr(server, "_prepare_publish", lambda *a, **k: None)
    monkeypatch.setattr(server, "api_thumbcopilot_generate", lambda *a, **k: None)
    monkeypatch.setattr(server, "_drive_copy_later", lambda *a, **k: None)
    monkeypatch.setattr(server._mqa, "start", lambda *a, **k: None)
    monkeypatch.setattr(yt_api, "configured", lambda: False)
    monkeypatch.setattr(rc, "stitch", lambda chs, out: (open(out, "wb").write(b"v"),
                                                         [(n, 600.0 * i) for i, (n, _p, _x) in enumerate(chs)])[1])
    monkeypatch.setattr(rc, "_probe", lambda path: 600.0)
    monkeypatch.setenv("AUTO_APPEND", "1")
    monkeypatch.setenv("AUTO_APPEND_MODE", "cumulative")
    monkeypatch.setenv("AUTO_APPEND_MIN", "1")
    server._AUTO_APPEND["seen"].clear()
    return root


def test_new_chapter_extends_the_queued_merge_and_takes_its_place_once_approved(lib):
    root = lib
    for n in (1, 2):
        _chapter(root, "dr", n)
    for other in ("aa_7", "zz_9"):
        os.makedirs(os.path.join(root, other, "exports"))
    server.api_range_build(server.RangeIn(projects=["dr_1", "dr_2"]))
    assert _wait(root, "dr_ch1-2")["build"]["status"] == "done"
    # the owner approved 1-2; it sits between two other videos on the schedule
    revs = server.load_reviews(os.path.join(root, "dr_ch1-2"))
    revs["final_range_1-2.mp4"] = {**(revs.get("final_range_1-2.mp4") or {}), "status": "approved"}
    server.save_reviews(os.path.join(root, "dr_ch1-2"), revs)
    pq.add(root, [{"project": "aa_7", "name": "a.mp4"}])
    pq.add(root, [{"project": "dr_ch1-2", "name": "final_range_1-2.mp4"}])
    pq.add(root, [{"project": "zz_9", "name": "z.mp4"}])

    _chapter(root, "dr", 3)
    d = next(x for x in server._auto_append_pass() if x["series"] == "dr")
    assert d["action"] == "build" and (d["start"], d["end"]) == (1, 3), d
    assert d["extends"] == ["dr_ch1-2"] and d["retire"] == []
    m = _wait(root, "dr_ch1-3")
    assert m["build"]["status"] == "done", m
    assert [x["project"] for x in m["merged_from"]] == ["dr_1", "dr_2", "dr_3"]
    queued = [x["project"] for x in pq.load(root)["items"] if x["status"] == "queued"]
    assert queued == ["aa_7", "dr_ch1-2", "zz_9"]                 # 1-2 still posts if 1-3 isn't approved
    again = next(x for x in server._auto_append_pass() if x["series"] == "dr")
    assert again["action"] == "none" and "already has chapters 1-3" in again["why"]

    # approving 1-3 puts it where 1-2 was; 1-2 goes to Deleted
    revs = server.load_reviews(os.path.join(root, "dr_ch1-3"))
    revs["final_range_1-3.mp4"] = {**(revs.get("final_range_1-3.mp4") or {}), "status": "approved"}
    server.save_reviews(os.path.join(root, "dr_ch1-3"), revs)
    assert server._schedule_on_verdict("dr_ch1-3", "final_range_1-3.mp4", "approved")
    queued = [x["project"] for x in pq.load(root)["items"] if x["status"] == "queued"]
    assert queued == ["aa_7", "dr_ch1-3", "zz_9"]
    assert not os.path.isdir(os.path.join(root, "dr_ch1-2"))
    assert any(n.startswith("dr_ch1-2__") for n in os.listdir(server._trash_root()))
    for n in (1, 2, 3):                                           # the pieces belong to 1-3
        rv = server.load_reviews(os.path.join(root, f"dr_{n}"))["final.mp4"]
        assert rv["status"] == "merged" and rv["merged_into"] == "dr_ch1-3"


def test_auto_merge_waits_while_another_merge_is_building(lib, monkeypatch):
    root = lib
    for n in (1, 2):
        _chapter(root, "dr", n)
    server.api_range_build(server.RangeIn(projects=["dr_1", "dr_2"]))
    _wait(root, "dr_ch1-2")
    _chapter(root, "dr", 3)
    monkeypatch.setattr(server, "_RANGE_BUILDING", {"other_ch1-9"})
    d = next(x for x in server._auto_append_pass() if x["series"] == "dr")
    assert d["action"] == "wait" and "another merged video is being built" in d["why"]
    assert not os.path.isdir(os.path.join(root, "dr_ch1-3"))


def test_auto_merge_waits_for_disk_room(lib, monkeypatch):
    root = lib
    for n in (1, 2):
        _chapter(root, "dr", n)
    server.api_range_build(server.RangeIn(projects=["dr_1", "dr_2"]))
    _wait(root, "dr_ch1-2")
    _chapter(root, "dr", 3)
    monkeypatch.setattr(server, "MERGE_HEADROOM_GB", 10 ** 6)
    d = next(x for x in server._auto_append_pass() if x["series"] == "dr")
    assert d["action"] == "wait" and "not enough free disk" in d["why"]
