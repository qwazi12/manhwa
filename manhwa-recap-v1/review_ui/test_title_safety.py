"""Owner titles must survive automation (Mount Hua 181-184 posted the template
title; Too Many Heroes 32-33 was reset every ~10 minutes, 2026-10-27/28)."""
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
import server

PKG = {"title": "[32-33] TEMPLATE TITLE | Flamingo Recap", "description": "pkg desc", "tags": ["pkg"]}


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    d = tmp_path / "series_ch32-33"
    (d / "exports").mkdir(parents=True)
    (d / "exports" / "final_range_32-33.mp4").write_bytes(b"mp4")
    monkeypatch.setattr(server, "_publish_defaults_base",
                        lambda p: {"title": "default title", "description": "default desc", "tags": []})
    monkeypatch.setattr(server, "_seo_chapter_rec", lambda p: {})
    return str(d)


def save(pdir, name, md):
    store = server.load_publish(pdir); store[name] = md; server.save_publish(pdir, store)


def test_automatic_package_keeps_the_owners_title_but_refreshes_untouched_fields(chapter):
    name = "final_range_32-33.mp4"
    out = server._apply_chapter_package(chapter, name, PKG, keep_owner_edits=True)   # first auto fill
    assert out["title"] == PKG["title"]
    md = server.load_publish(chapter)[name]
    md["title"] = "[32-33] OWNER TITLE | Flamingo Recap"                            # owner edits
    save(chapter, name, md)
    newer = {**PKG, "description": "newer pkg desc"}
    out = server._apply_chapter_package(chapter, name, newer, keep_owner_edits=True)  # next auto pass
    stored = server.load_publish(chapter)[name]
    assert stored["title"] == "[32-33] OWNER TITLE | Flamingo Recap"
    assert stored["description"] == "newer pkg desc"                                # untouched -> refreshed
    assert out["_applied"] == ["description", "tags"]


def test_explicit_regenerate_still_replaces(chapter):
    name = "final_range_32-33.mp4"
    save(chapter, name, {"title": "OWNER TITLE"})
    server._apply_chapter_package(chapter, name, PKG)                               # AI button path
    assert server.load_publish(chapter)[name]["title"] == PKG["title"]


def test_owner_title_typed_before_any_automation_is_kept(chapter):
    name = "final_range_32-33.mp4"
    save(chapter, name, {"title": "OWNER FIRST"})
    server._apply_chapter_package(chapter, name, PKG, keep_owner_edits=True)
    assert server.load_publish(chapter)[name]["title"] == "OWNER FIRST"


def test_draft_form_writes_through_to_the_existing_video(chapter, monkeypatch):
    from fastapi.testclient import TestClient
    import ingest
    monkeypatch.setattr(ingest, "PROJECTS", str(Path(chapter).parent))
    monkeypatch.setattr(server, "_publish_payload", lambda p, pid, n, md: {"metadata": md})
    r = TestClient(server.app).post("/api/publish", json={
        "project": "series_ch32-33", "name": "_draft", "metadata": {"title": "[32-33] TYPED IN DRAFT"}})
    assert r.status_code == 200
    store = server.load_publish(chapter)
    assert store["_draft"]["title"] == "[32-33] TYPED IN DRAFT"
    assert store["final_range_32-33.mp4"]["title"] == "[32-33] TYPED IN DRAFT"


def test_prep_pass_does_not_loop_on_a_locked_series_video(chapter, monkeypatch):
    name = "final_range_32-33.mp4"
    save(chapter, name, {"title": "OWNER TITLE"})
    calls = []
    monkeypatch.setattr(server, "_chapter_rows", lambda: [{"id": "series_ch32-33", "status": {"key": "scheduled"}}])
    monkeypatch.setattr(server, "project_dir_for", lambda pid: chapter)
    monkeypatch.setattr(server, "_series_locked", lambda p: ({}, {"series_hook": "x", "approved_by_user": True}))
    monkeypatch.setattr(server, "_budget_left", lambda: 5.0)
    monkeypatch.setattr(server, "_prepare_publish", lambda p, n, do_seo=True: calls.append((n, do_seo)))
    import thumbnail
    monkeypatch.setattr(thumbnail, "path_for", lambda p, n: "thumb.jpg")
    monkeypatch.setattr(server, "_PREP_TRIED", {})
    server._publish_prep_pass(); server._publish_prep_pass()
    assert calls == []                                  # titled, locked, thumbnail present: nothing to do
    monkeypatch.setattr(thumbnail, "path_for", lambda p, n: None)   # thumbnail missing
    server._publish_prep_pass(); server._publish_prep_pass(); server._publish_prep_pass()
    assert calls == [(name, False)]                    # once, not every pass; no SEO rewrite


def test_a_system_written_fallback_title_is_still_replaced_by_the_package(chapter):
    name = "final_range_32-33.mp4"
    fallback = "[32-33] Too Many Heroes Chapters 32-33 | Flamingo Recap"
    save(chapter, name, {"title": fallback, "description": "d", "tags": [],
                         "_auto": {"title": fallback, "description": "d", "tags": []}})
    server._apply_chapter_package(chapter, name, PKG, keep_owner_edits=True)
    assert server.load_publish(chapter)[name]["title"] == PKG["title"]
