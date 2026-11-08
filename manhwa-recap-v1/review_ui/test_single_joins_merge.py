"""Owner, 2026-11-08: Stellar 136 was approved as a single right after the unposted
129-135 merge. Approving a single of a series posted as merges is refused unless
allow_single (or an AUTO_APPEND_HOLD test) says it posts alone."""
import json
import os

import pytest
from fastapi import HTTPException

import auto_append as aa
import ingest
import server


def _proj(root, pid, **meta):
    d = os.path.join(root, pid)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    with open(os.path.join(d, "project.json"), "w") as f:
        json.dump({"id": pid, "series": "Star", "series_id": "star", **meta}, f)
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
    monkeypatch.setattr(server, "_schedule_on_verdict", lambda *a: False)
    monkeypatch.delenv("AUTO_APPEND_HOLD", raising=False)
    monkeypatch.setenv("AUTO_APPEND_MIN", "7")
    _posted(_proj(root, "star_ch122-128", kind="range", chapters_start=122, chapters_end=128, chapter="122-128"),
            "final_range_122-128.mp4")
    m = _proj(root, "star_ch129-135", kind="range", chapters_start=129, chapters_end=135, chapter="129-135",
              build={"status": "done", "export": "final_range_129-135.mp4"})
    _export(m, "final_range_129-135.mp4")
    for n in (136, 140):
        _export(_proj(root, f"star_{n}", chapter=str(n)), "final.mp4")
    return root


def _approve(pid, **kw):
    return server.api_review_save(server.ReviewIn(project=pid, name="final.mp4", status="approved", **kw))


def test_single_after_an_unposted_merge_is_refused(lib):
    with pytest.raises(HTTPException) as e:
        _approve("star_136")
    assert e.value.status_code == 409 and "belongs in the merge 129-135" in e.value.detail


def test_single_after_the_posted_merge_waits_for_the_next_merge(lib):
    with pytest.raises(HTTPException) as e:
        _approve("star_140")
    assert "waits for the next merge, which starts at 129" in e.value.detail


def test_allow_single_and_hold_post_it_alone(lib, monkeypatch):
    assert _approve("star_136", allow_single=True)["ok"]
    monkeypatch.setenv("AUTO_APPEND_HOLD", "star:140")
    assert _approve("star_140")["ok"]


def test_sent_back_merge_does_not_count(lib):
    rd = os.path.join(lib, "star_ch129-135")
    with open(os.path.join(rd, "reviews.json"), "w") as f:
        json.dump({"final_range_129-135.mp4": {"status": "sent_back"}}, f)
    with pytest.raises(HTTPException) as e:
        _approve("star_136")
    assert "waits for the next merge" in e.value.detail


def test_series_of_singles_is_not_affected(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    monkeypatch.setattr(server, "_schedule_on_verdict", lambda *a: False)
    _posted(_proj(str(tmp_path), "solo_5", chapter="5"), "final.mp4")
    _export(_proj(str(tmp_path), "solo_6", chapter="6"), "final.mp4")
    assert _approve("solo_6")["ok"]


def test_wait_message_names_the_chapter_after_an_approved_merge():
    posted = [{"project": "p", "name": "x", "start": 122, "end": 128, "at": 1}]
    singles = {n: {"project": f"s{n}", "name": "f", "has_video": True, "status": "merged",
                   "merged_into": "m"} for n in range(129, 136)}
    singles[136] = {"project": "s136", "name": "f", "has_video": True, "status": "review_pending"}
    pending = [{"project": "m", "start": 129, "end": 135, "building": False, "approved": True}]
    d = aa.decide(posted, singles, pending, min_new=7)
    assert d["action"] == "wait" and "ch 136 ready right after it" in d["why"] and "rebuild 129-136" in d["why"]
