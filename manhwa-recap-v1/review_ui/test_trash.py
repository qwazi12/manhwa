"""The Deleted tab: delete moves to a bin, restore brings it back (owner, 2026-10-14)."""
import json
import os

import pytest
from fastapi.testclient import TestClient

import ingest
import publish_queue as pq
import server
from test_merge_unmerge import _wait_built, lib   # noqa: F401  (fixture)


def test_delete_restore_and_delete_forever(lib):
    root, c = lib
    os.makedirs(os.path.join(root, "other_1"), exist_ok=True)
    with open(os.path.join(root, "other_1", "segments.json"), "w") as f:
        f.write("[]")
    server.activate_project(server.ActivateIn(id="other_1"))
    pq.remove(root, "q25")                       # a scheduled video can't be deleted
    r = c.post("/api/projects/delete", json={"ids": ["hound_25"]}).json()
    assert "deleted" in r, r
    assert r["deleted"] == ["hound_25"] and not os.path.isdir(os.path.join(root, "hound_25"))
    items = c.get("/api/trash").json()["items"]
    it = next(i for i in items if i["project"] == "hound_25")
    assert it["can_restore"] and it["days_left"] == 14 and it["videos"] == 1
    assert c.post("/api/trash/restore", json={"name": it["name"]}).status_code == 200
    assert os.path.isdir(os.path.join(root, "hound_25"))
    assert "hound_25" not in (json.load(open(os.path.join(root, "_gone.json"))) if os.path.exists(os.path.join(root, "_gone.json")) else {})
    c.post("/api/projects/delete", json={"ids": ["hound_25"]})
    name = next(i["name"] for i in c.get("/api/trash").json()["items"] if i["project"] == "hound_25")
    assert c.post("/api/trash/purge", json={"name": name}).json()["ok"]
    assert all(i["project"] != "hound_25" for i in c.get("/api/trash").json()["items"])


def test_deleting_a_merged_video_releases_its_chapters_and_restoring_takes_them_back(lib):
    root, c = lib
    r1 = c.post("/api/ranges/build", json={"projects": ["hound_23", "hound_24"]}).json()["project"]
    _wait_built(root, r1)
    rv = lambda p: json.load(open(os.path.join(root, p, "reviews.json")))["final.mp4"]
    assert rv("hound_23")["status"] == "merged"
    assert c.post("/api/projects/delete", json={"ids": [r1]}).json()["deleted"] == [r1]
    assert rv("hound_23") == {"status": "approved", "note": "ok 23"}                 # released
    assert {it["id"]: it["status"] for it in pq.load(root)["items"]}["q23"] == "queued"
    name = next(i["name"] for i in c.get("/api/trash").json()["items"] if i["project"] == r1)
    assert c.post("/api/trash/restore", json={"name": name}).status_code == 200
    assert rv("hound_23")["status"] == "merged" and rv("hound_23")["merged_into"] == r1
    assert {it["id"]: it["status"] for it in pq.load(root)["items"]}["q23"] == "removed"
    # and it can still be unmerged afterwards
    assert c.post("/api/ranges/unmerge", json={"project": r1}).status_code == 200
    assert rv("hound_23") == {"status": "approved", "note": "ok 23"}


def test_release_orphans(lib):
    root, c = lib
    with open(os.path.join(root, "hound_23", "reviews.json"), "w") as f:
        json.dump({"final.mp4": {"status": "merged", "merged_into": "hound_ch23-24", "note": "x"}}, f)
    r = c.post("/api/ranges/release_orphans", json={}).json()
    assert r["applied"] is False and [x["project"] for x in r["chapters"]] == ["hound_23"]
    c.post("/api/ranges/release_orphans", json={"apply": True})
    assert json.load(open(os.path.join(root, "hound_23", "reviews.json")))["final.mp4"] == {"note": "x"}
