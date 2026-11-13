"""Series defaults must not rewrite a video a person has signed off (review,
2026-11-13): "apply to all" rewrote title / description / tags / channels of
every stored video of the series, approved and posted ones included."""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest  # noqa: E402
import server  # noqa: E402

OLD = {"title": "Reviewed title", "description": "reviewed", "tags": ["reviewed"], "targets": ["mk:youtube"]}
PACK = {"title_template": "[{chapter}] NEW TEMPLATE", "description_template": "new {chapter}",
        "default_tags": ["new"], "default_targets": ["other:youtube"]}


def _chapter(root, n, review=None, publishes=None, details=True):
    d = os.path.join(root, f"s_{n}")
    os.makedirs(os.path.join(d, "exports"))
    json.dump({"id": f"s_{n}", "series": "S", "series_id": "s", "chapter": str(n)}, open(os.path.join(d, "project.json"), "w"))
    if details:
        json.dump({"final.mp4": dict(OLD)}, open(os.path.join(d, server.PUBLISH_NAME), "w"))
    if review:
        json.dump({"final.mp4": {"status": review}}, open(os.path.join(d, "reviews.json"), "w"))
    if publishes:
        json.dump({"final.mp4": publishes}, open(os.path.join(d, "publishes.json"), "w"))
    return d


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "_rerender_chosen_thumbnails", lambda **k: None)
    return root


def _details(d):
    return server.load_publish(d)


def test_only_unreviewed_videos_take_the_new_defaults(lib):
    pending = _chapter(lib, 1, review="review_pending")
    approved = _chapter(lib, 2, review="approved")
    posted = _chapter(lib, 3, publishes={"status": "published", "results": [{"status": "published"}]})
    posting = _chapter(lib, 4, publishes={"status": "in_progress", "results": []})
    queued = _chapter(lib, 5)
    server._pq._save(lib, {"items": [{"id": "q1", "project": "s_5", "name": "final.mp4", "status": "queued"}]})
    applied = server._propagate_series_defaults("s", "S", PACK)

    assert _details(pending)["final.mp4"]["title"] == "[1] NEW TEMPLATE"
    assert _details(pending)["final.mp4"]["targets"] == ["other:youtube"]
    for d in (approved, posted, posting):
        assert _details(d)["final.mp4"] == OLD, d
    # the draft (details for a future render) still follows the series
    assert _details(approved)[server.DRAFT]["title"] == "[2] NEW TEMPLATE"
    assert server._PROPAGATE_LAST["protected"] == 4 and applied == 5
    assert _details(queued)["final.mp4"] == OLD


def test_signed_off_video_without_its_own_details_keeps_the_draft_it_reads(lib):
    d = _chapter(lib, 6, review="approved", details=False)
    json.dump({server.DRAFT: dict(OLD)}, open(os.path.join(d, server.PUBLISH_NAME), "w"))
    server._propagate_series_defaults("s", "S", PACK)
    assert _details(d)[server.DRAFT] == OLD


def test_locked_reasons(lib):
    d = _chapter(lib, 7, review="approved")
    assert server._metadata_locked(d, set()) == {"final.mp4": "approved"}
    assert server._metadata_locked(d, {("s_7", "other.mp4")})["other.mp4"] == "in the posting queue"
    assert server._metadata_locked(_chapter(lib, 8, review="sent_back"), set()) == {}
