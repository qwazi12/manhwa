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


# ---- 2026-11-14: the lock is enforced where details are stored, for every
# automatic writer, and covers thumbnails and the upload thumbnail too

def _history(d):
    try:
        return [json.loads(x) for x in open(os.path.join(d, server.PACKAGING_LOG)).read().splitlines()]
    except OSError:
        return []


def test_any_automatic_writer_is_stopped_at_the_store(lib):
    d = _chapter(lib, 20, review="approved")
    store = server.load_publish(d)
    store["final.mp4"] = {**store["final.mp4"], "title": "REWRITTEN BY A JOB", "targets": ["other:youtube"]}
    server.save_publish(d, store)                              # actor defaults to "auto"
    assert _details(d)["final.mp4"] == OLD
    h = _history(d)[-1]
    assert h["blocked"] == "approved" and h["actor"] == "auto" and set(h["fields"]) == {"title", "targets"}
    # a person editing that video is allowed, and it is recorded
    store = server.load_publish(d)
    store["final.mp4"] = {**store["final.mp4"], "title": "Owner's new title"}
    server.save_publish(d, store, actor="owner", why="edited")
    assert _details(d)["final.mp4"]["title"] == "Owner's new title"
    h = _history(d)[-1]
    assert h["actor"] == "owner" and h["title"] == ["Reviewed title", "Owner's new title"] and "blocked" not in h


def test_unlocked_videos_still_take_automatic_changes(lib):
    d = _chapter(lib, 21, review="review_pending")
    store = server.load_publish(d)
    store["final.mp4"]["title"] = "auto SEO"
    server.save_publish(d, store)
    assert _details(d)["final.mp4"]["title"] == "auto SEO" and _history(d)[-1]["fields"] == ["title"]


@pytest.mark.parametrize("state", ["approved", "queued", "being posted", "scheduled", "posted", "unconfirmed"])
def test_every_locked_state_is_protected(lib, state):
    pubs = {"being posted": {"status": "in_progress", "results": []},
            "scheduled": {"status": "published", "results": [{"status": "published", "visibility": "scheduled"}]},
            "posted": {"status": "published", "results": [{"status": "published"}]},
            "unconfirmed": {"status": "failed", "results": [{"status": "unconfirmed"}]}}.get(state)
    d = _chapter(lib, 30, review="approved" if state == "approved" else None, publishes=pubs)
    if state == "queued":
        server._pq._save(lib, {"items": [{"id": "q", "project": "s_30", "name": "final.mp4", "status": "queued"}]})
    assert "final.mp4" in server._metadata_locked(d)
    store = server.load_publish(d)
    store["final.mp4"] = {"title": "changed by a job"}
    server.save_publish(d, store)
    assert _details(d)["final.mp4"] == OLD
    server._propagate_series_defaults("s", "S", PACK)
    assert _details(d)["final.mp4"] == OLD


def test_retroactive_update_needs_a_preview_and_named_videos(lib, monkeypatch):
    import series_pack
    approved, pending = _chapter(lib, 40, review="approved"), _chapter(lib, 41, review="review_pending")
    monkeypatch.setattr(series_pack, "load", lambda root, sid: PACK if sid == "s" else None)
    pre = server.api_series_defaults_retroactive(server.SeriesRetroIn(series_id="s"))
    assert pre["applied"] == 0 and _details(approved)["final.mp4"] == OLD          # the preview changes nothing
    row = next(x for x in pre["preview"] if (x["project"], x["name"]) == ("s_40", "final.mp4"))
    assert row["locked"] == "approved" and row["changes"]["title"] == ["Reviewed title", "[40] NEW TEMPLATE"]
    assert next(x for x in pre["preview"] if x["project"] == "s_41" and x["name"] == "final.mp4")["locked"] is None
    with pytest.raises(server.HTTPException):                                        # apply without naming videos
        server.api_series_defaults_retroactive(server.SeriesRetroIn(series_id="s", apply=True))
    out = server.api_series_defaults_retroactive(
        server.SeriesRetroIn(series_id="s", apply=True, exports=[["s_40", "final.mp4"]]))
    assert out["applied"] == 1 and _details(approved)["final.mp4"]["title"] == "[40] NEW TEMPLATE"
    assert _details(pending)["final.mp4"] == OLD                                    # only the named video changed
    assert _history(approved)[-1]["actor"] == "owner"


def _thumb(d, name, colour, source="auto"):
    import io
    import thumbnail as tb
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1280, 720), colour).save(buf, "JPEG")
    tb.save(d, name, buf.getvalue(), source=source)
    return server._thumb_sha(d, name)


def test_bulk_thumbnail_redraws_leave_locked_videos_alone(lib, monkeypatch):
    approved, pending = _chapter(lib, 50, review="approved"), _chapter(lib, 51, review="review_pending")
    for d in (approved, pending):
        open(os.path.join(d, "exports", "final.mp4"), "wb").write(b"v")
        _thumb(d, "final.mp4", (10, 20, 30))
    masters = lambda sid: False
    assert server._thumb_keep_reason(approved, "final.mp4", masters) == "approved"
    assert server._thumb_keep_reason(pending, "final.mp4", masters) is None
    before = server._thumb_sha(approved, "final.mp4")
    # the automatic apply (publish preparation / series look) is refused for a locked video
    out = server._thumbcopilot_apply(server.ThumbApplyIn(project="s_50", name="final.mp4", concept_id="x"))
    assert out["skipped"] == "approved" and server._thumb_sha(approved, "final.mp4") == before
    assert _history(approved)[-1]["fields"] == ["thumbnail"] and _history(approved)[-1]["blocked"] == "approved"


def test_approval_pins_the_upload_thumbnail_and_the_upload_uses_it(lib, monkeypatch):
    d = _chapter(lib, 60)
    open(os.path.join(d, "exports", "final.mp4"), "wb").write(b"v")
    _thumb(d, "final.mp4", (200, 10, 10))
    calls = []

    def master(pdir, fallback):                      # the series master + badge, as worked out at that moment
        calls.append(fallback)
        out = os.path.join(pdir, "exports", "_thumbs", f"series_master_{len(calls)}.jpg")
        open(out, "wb").write(f"master v{len(calls)}".encode())
        return out
    monkeypatch.setattr(server, "_series_master_thumb", master)
    rec = {}
    pin = server._pin_packaging(d, "final.mp4", rec)
    assert pin["series_master"] is True and pin["upload_thumb"] and len(calls) == 1
    server.save_reviews(d, {"final.mp4": {"status": "approved", **rec}})
    pinned = server._pinned_upload_thumb(d, "final.mp4")
    assert open(pinned, "rb").read() == b"master v1"
    # the master changes in Drive after approval: the approved file is still what uploads
    assert server._pinned_upload_thumb(d, "final.mp4") == pinned and len(calls) == 1
    # the pinned file is tampered with: it is not trusted
    open(pinned, "wb").write(b"something else")
    assert server._pinned_upload_thumb(d, "final.mp4") is None
    # a person replaces the thumbnail after approval: the pin is dropped and recorded
    open(pinned, "wb").write(b"master v1")
    before = server._thumb_sha(d, "final.mp4")
    _thumb(d, "final.mp4", (10, 200, 10), source="upload")
    server._thumbnail_changed(d, "final.mp4", before, "owner", "thumbnail uploaded by hand")
    assert server._pinned_upload_thumb(d, "final.mp4") is None
    assert _history(d)[-1]["fields"] == ["thumbnail"] and _history(d)[-1]["actor"] == "owner"


def test_tag_trim_and_startup_repairs_do_not_touch_a_locked_video(lib):
    d = _chapter(lib, 70, review="approved")
    store = server.load_publish(d)
    store["final.mp4"]["tags"] = ["shorter"]
    server.save_publish(d, store, why="tag trim")
    assert _details(d)["final.mp4"]["tags"] == ["reviewed"]


def test_late_automatic_seo_fills_only_what_is_still_empty(lib):
    """SEO that arrives after approval (e.g. once the spend day resets) may
    fill an empty field; it never replaces a value a person approved."""
    d = _chapter(lib, 80, review="approved")
    store = server.load_publish(d)
    store["final.mp4"] = {"title": "Reviewed title", "description": "", "tags": []}
    server.save_publish(d, store, actor="owner")
    store = server.load_publish(d)
    store["final.mp4"] = {"title": "SEO TITLE", "description": "seo description", "tags": ["seo"]}
    server.save_publish(d, store, why="automatic SEO fill-in")
    got = _details(d)["final.mp4"]
    assert got == {"title": "Reviewed title", "description": "seo description", "tags": ["seo"]}
    h = _history(d)
    assert h[-2]["blocked"] == "approved" and h[-2]["fields"] == ["title"]
    assert h[-1]["filled_empty"] is True and h[-1]["fields"] == ["description", "tags"]


def test_automatic_seo_apply_is_not_treated_as_the_owner(lib):
    assert getattr(server._SEO_ACTOR, "who", "owner") == "owner"      # a click on the page is the owner
