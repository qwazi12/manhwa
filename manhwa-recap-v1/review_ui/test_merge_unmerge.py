"""Merge, add to a merged video, and unmerge (owner, 2026-10-12)."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

import ingest
import publish_queue as pq
import range_compile as rc
import server


def _wait_built(root, pid):
    for _ in range(100):
        m = json.load(open(os.path.join(root, pid, "project.json")))
        if (m.get("build") or {}).get("status") in ("done", "error"):
            return m
        time.sleep(0.05)
    raise AssertionError("build never finished")


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "_yt_root", lambda: os.path.join(root, "_yt"))
    monkeypatch.setattr(server, "_prepare_publish", lambda *a, **k: None)
    monkeypatch.setattr(server, "api_thumbcopilot_generate", lambda *a, **k: None)
    monkeypatch.setattr(rc, "stitch", lambda chs, out: (open(out, "wb").write(b"v"),
                                                         [(n, 60.0 * i) for i, (n, _p, _x) in enumerate(chs)])[1])
    monkeypatch.setattr(rc, "_probe", lambda path: 60.0)
    for ch in (23, 24, 25):
        d = tmp_path / f"hound_{ch}"
        (d / "exports").mkdir(parents=True)
        (d / "exports" / "final.mp4").write_bytes(b"v")
        (d / "project.json").write_text(json.dumps({"id": f"hound_{ch}", "series": "Hound", "series_id": "hound",
                                                    "chapter": str(ch)}))
        (d / "reviews.json").write_text(json.dumps({"final.mp4": {"status": "approved", "note": f"ok {ch}"}}))
    pq._save(root, {"items": [
        {"id": "q23", "project": "hound_23", "name": "final.mp4", "status": "queued"},
        {"id": "x1", "project": "other_1", "name": "final.mp4", "status": "queued"},
        {"id": "q24", "project": "hound_24", "name": "final.mp4", "status": "queued"},
        {"id": "q25", "project": "hound_25", "name": "final.mp4", "status": "queued"}]})
    return root, TestClient(server.app)


def _rev(root, pid):
    try:
        revs = json.load(open(os.path.join(root, pid, "reviews.json")))
    except OSError:
        return None
    return revs.get("final.mp4") or next(iter(revs.values()), None)


def test_merge_then_add_a_chapter_then_unmerge_step_by_step(lib):
    root, c = lib
    r = c.post("/api/ranges/build", json={"projects": ["hound_23", "hound_24"]})
    assert r.status_code == 200, r.text
    r1 = r.json()["project"]
    _wait_built(root, r1)
    assert _rev(root, "hound_23")["status"] == "merged"
    # the merged video joins a new merge with the next chapter
    r = c.post("/api/ranges/build", json={"projects": [r1, "hound_25"]})
    assert r.status_code == 200, r.text
    r2 = r.json()["project"]
    m2 = _wait_built(root, r2)
    assert (m2["chapters_start"], m2["chapters_end"]) == (23, 25)
    assert [int(k) for k, _t in m2["build"]["marks"]] == [23, 24, 25]     # inner markers kept
    assert _rev(root, r1)["status"] == "merged" and _rev(root, "hound_25")["status"] == "merged"
    # unmerge the big one: 25 and the first merged video come back
    r = c.post("/api/ranges/unmerge", json={"project": r2})
    assert r.status_code == 200, r.text
    assert not os.path.isdir(os.path.join(root, r2))
    assert any(n.startswith("_unmerged_" + r2) for n in os.listdir(root))            # kept aside
    assert _rev(root, "hound_25") == {"status": "approved", "note": "ok 25"}
    assert _rev(root, r1) is None                     # the first merged video is back to un-reviewed
    q = {it["id"]: it["status"] for it in pq.load(root)["items"]}
    assert q["q25"] == "queued"
    # unmerge the first: 23 and 24 back, in their old places
    r = c.post("/api/ranges/unmerge", json={"project": r1})
    assert r.status_code == 200, r.text
    assert _rev(root, "hound_23") == {"status": "approved", "note": "ok 23"}
    order = [it["id"] for it in pq.load(root)["items"] if it["status"] == "queued"]
    assert order == ["q23", "x1", "q24", "q25"]


def test_gaps_and_overlaps_are_refused(lib):
    root, c = lib
    r = c.post("/api/ranges/build", json={"projects": ["hound_23", "hound_25"]})
    assert r.status_code == 400 and "unbroken" in r.json()["detail"]
    r1 = c.post("/api/ranges/build", json={"projects": ["hound_23", "hound_24"]}).json()["project"]
    _wait_built(root, r1)
    r = c.post("/api/ranges/build", json={"projects": [r1, "hound_24"]})
    assert r.status_code == 400 and "overlap" in r.json()["detail"]


def test_a_posted_merged_video_is_not_unmerged(lib):
    root, c = lib
    r1 = c.post("/api/ranges/build", json={"projects": ["hound_23", "hound_24"]}).json()["project"]
    _wait_built(root, r1)
    with open(os.path.join(root, r1, "publishes.json"), "w") as f:
        json.dump({"x.mp4": {"url": "https://youtu.be/x"}}, f)
    r = c.post("/api/ranges/unmerge", json={"project": r1})
    assert r.status_code == 409 and "already posted" in r.json()["detail"]


def test_merged_video_gets_the_full_chapter_package(lib, monkeypatch):
    root, c = lib
    import series_pack as sp
    sp.save(os.path.join(root, "_yt"), {"series_id": "hound", "series_name_en": "Hound", "playlist_id": "PLabcdefghij123",
                                         "title_lock": {"series_hook": "Disposable Hound Returns For VENGEANCE",
                                                        "variant": "long", "approved_by_user": True}})
    monkeypatch.setattr(server, "_competitor_check", lambda *a: {})
    r1 = c.post("/api/ranges/build", json={"projects": ["hound_23", "hound_24", "hound_25"]}).json()["project"]
    m = _wait_built(root, r1)
    pkg = server._build_chapter_package(os.path.join(root, r1), m["build"]["export"], None, competitor=False)
    assert pkg["title"] == "[23-25] Disposable Hound Returns For VENGEANCE — Hound | Flamingo Recap"
    assert {"hound chapter 23", "hound chapter 24", "hound chapter 25"} <= set(pkg["tags"])
    assert "Hound Chapters 23-25 recap (English)" in pkg["description"]
    assert "• 0:00 - Chapter 23" in pkg["description"] and "• 1:00 - Chapter 24" in pkg["description"]
    assert pkg["playlist_id"] == "PLabcdefghij123" and "PLabcdefghij123" in pkg["first_comment"]
    assert pkg["problems"] == []


def test_repair_titles_dry_run_then_apply(lib):
    root, c = lib
    import series_pack as sp
    yt = os.path.join(root, "_yt")
    sp.save(yt, {"series_id": "hound", "title_template": "[32-33]His Infinite REGRESSION - Hound Ch. {chapter}",
                 "description_template": "Hound Chapter 32-33 recap"})
    with open(os.path.join(root, "hound_23", "publish.json"), "w") as f:
        json.dump({"final.mp4": {"title": "[32-33]His Infinite REGRESSION - Hound Ch. 23",
                                 "description": "Hound Chapter 32-33 recap", "tags": ["hound chapter 32-33", "x"]},
                   "_draft": {"title": "[23] Hook — Hound | Manhwa Recap", "seo_v2": True}}, f)
    r = c.post("/api/publish/repair_titles", json={}).json()
    assert r["applied"] is False and len(r["changes"]) >= 5
    assert json.load(open(os.path.join(root, "hound_23", "publish.json")))["final.mp4"]["title"].startswith("[32-33]")
    r = c.post("/api/publish/repair_titles", json={"apply": True}).json()
    md = json.load(open(os.path.join(root, "hound_23", "publish.json")))
    assert md["final.mp4"]["title"] == "[23]His Infinite REGRESSION - Hound"
    assert md["final.mp4"]["description"] == "Hound Chapter 23 recap"
    assert md["final.mp4"]["tags"][0] == "hound chapter 23" and "x" in md["final.mp4"]["tags"]
    assert md["_draft"]["title"] == "[23] Hook — Hound"
    pk = sp.load(yt, "hound")
    assert pk["title_template"] == "[{chapter}]His Infinite REGRESSION - Hound"
    assert pk["description_template"] == "Hound Chapter {chapter} recap"
