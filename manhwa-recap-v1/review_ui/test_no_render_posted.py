"""Owner, 2026-11-08: Fog Land 45 was rendered again after the 38-45 merge had posted.
A chapter already inside a posted video is not rendered, not in Video review and not in
the PENDING export, unless allow_posted is sent."""
import json
import os

import pytest
from fastapi import HTTPException

import ingest
import render_queue as rq
import server


def _proj(root, pid, **meta):
    d = os.path.join(root, pid)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    with open(os.path.join(d, "project.json"), "w") as f:
        json.dump({"id": pid, "series": "Fog", "series_id": "fog", **meta}, f)
    return d


def _export(d, name):
    open(os.path.join(d, "exports", name), "wb").write(b"v")


def _posted(d, name):
    _export(d, name)
    with open(os.path.join(d, "publishes.json"), "w") as f:
        json.dump({name: {"results": [{"status": "published", "network": "youtube",
                                       "url": "https://youtu.be/abcdefghijk", "published_at": 1}]}}, f)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    m = _proj(root, "fog_ch38-45", kind="range", chapters_start=38, chapters_end=45, chapter="38-45")
    _posted(m, "final_range_38-45.mp4")
    for n in (45, 46):
        d = _proj(root, f"fog_{n}", chapter=str(n))
        open(os.path.join(d, "segments.json"), "w").write("[]")
        _export(d, "final.mp4")
    return root


def test_cover_finds_the_posted_merge(lib):
    c = server._posted_cover(os.path.join(lib, "fog_45"))
    assert c["project"] == "fog_ch38-45" and (c["start"], c["end"]) == (38, 45)
    assert "already in the posted video 38-45" in c["why"]
    assert server._posted_cover(os.path.join(lib, "fog_46")) is None
    assert server._posted_cover(os.path.join(lib, "fog_ch38-45")) is None     # merges are not singles


def test_render_queue_skips_a_posted_chapter(lib, monkeypatch):
    import pipeline_steps as ps
    monkeypatch.setattr(ps, "status", lambda pdir: [{"key": "match", "state": "done"}])
    monkeypatch.setattr(server, "_chapter_busy", lambda pdir: None)
    monkeypatch.setattr(server, "_rq_kick", lambda: None)
    r = server.render_queue_add(server.RenderQueueIn(projects=["fog_45", "fog_46"]))
    assert r["added"] == ["fog_46"]
    assert r["skipped"][0]["id"] == "fog_45" and "posted video 38-45" in r["skipped"][0]["reason"]
    r = server.render_queue_add(server.RenderQueueIn(projects=["fog_45"], allow_posted=True))
    assert r["added"] == ["fog_45"]
    it = next(i for i in rq.load(lib)["items"] if i["project"] == "fog_45")
    assert it["allow_posted"] is True


def test_approve_refuses_a_posted_chapter(lib, monkeypatch):
    monkeypatch.setattr(server, "active_project_dir", lambda: os.path.join(lib, "fog_45"))
    monkeypatch.setattr(server, "_approval_path", lambda: os.path.join(lib, "fog_45", "approval.json"))
    with pytest.raises(HTTPException) as e:
        server.storyboard_approve(server.ApproveIn(approved=True))
    assert e.value.status_code == 409 and "posted video 38-45" in e.value.detail
    assert not os.path.exists(os.path.join(lib, "fog_45", "approval.json"))   # board not marked approved


def test_pending_export_leaves_out_a_posted_chapter(lib):
    p = server._pending_exports()
    assert "fog_46" in p and "fog_45" not in p


def test_chapter_row_is_not_video_review(lib):
    rows = {r["id"]: r for r in server._build_chapter_rows()}
    assert rows["fog_45"]["status"]["key"] == "posted"
    assert "posted video 38-45" in rows["fog_45"]["status"]["note"]
    assert rows["fog_46"]["status"]["key"] == "video_ready"
