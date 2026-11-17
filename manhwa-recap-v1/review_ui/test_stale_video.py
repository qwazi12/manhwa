"""Owner review, 2026-11-17 (issues waiting to happen): a video that no longer
matches its board could still be approved, merged or posted.
- A corrected spoken line whose new recording fits the old slot did not change
  the cut fingerprint, so an approved video stayed "current".
- A merged video (Regressor's Tale 30-38) still read "Watch the video ...
  Approve & schedule" after chapter 37's lines were rewritten.
No network. Run: python3 -m pytest test_stale_video.py -q"""
import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"


def _chapter(root, pid, lines, video="final.mp4", age=3600):
    pdir = os.path.join(root, pid)
    os.makedirs(os.path.join(pdir, "exports"))
    segs = [{"seg_index": i, "panel_id": f"page001_panel_{i + 1:03d}", "start": 0.0, "dur": 4.0, "end": 4.0,
             "user_included": True, "beats": [{"index": i, "text": t, "start": 0.0, "end": 3.0}]}
            for i, t in enumerate(lines)]
    json.dump(segs, open(os.path.join(pdir, "segments.json"), "w"))
    json.dump({"id": pid}, open(os.path.join(pdir, "project.json"), "w"))
    open(os.path.join(pdir, "exports", video), "wb").write(b"mp4")
    old = time.time() - age
    for f in ("segments.json", os.path.join("exports", video)):
        os.utime(os.path.join(pdir, f), (old, old))
    return pdir


def _edit_line(pdir, i, text):
    segs = json.load(open(os.path.join(pdir, "segments.json")))
    segs[i]["beats"][0]["text"] = text                       # same length slot: dur untouched
    json.dump(segs, open(os.path.join(pdir, "segments.json"), "w"))


@pytest.fixture
def studio(tmp_path, monkeypatch):
    import ingest
    import server
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server._mqa, "identity", lambda pdir, name: "same-file")
    ch37 = _chapter(root, "regressor_37", ["Eunhyun woke.", "Would you like a summary?"], "final_37.mp4")
    ch38 = _chapter(root, "regressor_38", ["He reached for the light."], "final_38.mp4")
    merged = _chapter(root, "regressor_ch37-38", [], "final_range_37-38.mp4", age=1800)
    json.dump({"id": "regressor_ch37-38", "kind": "range", "chapters_start": 37, "chapters_end": 38,
               "build": {"status": "done", "export": "final_range_37-38.mp4"},
               "merged_from": [{"project": "regressor_37", "name": "final_37.mp4"},
                               {"project": "regressor_38", "name": "final_38.mp4"}]},
              open(os.path.join(merged, "project.json"), "w"))
    return server, ch37, ch38, merged


def test_a_changed_spoken_line_makes_an_approved_video_out_of_date(studio):
    server, ch37, ch38, merged = studio
    recs = {"final_37.mp4": {"status": "approved", "cut_signature": server.cut_signature(pdir=ch37),
                             "text_signature": server.text_signature(ch37)}}
    server.save_reviews(ch37, recs)
    assert server.review_state(ch37, "final_37.mp4")["superseded"] is False
    assert server._approved_export(ch37) == "final_37.mp4"
    cut = server.cut_signature(pdir=ch37)
    _edit_line(ch37, 1, "He flew into the sky.")
    assert server.cut_signature(pdir=ch37) == cut                         # the old fingerprint cannot see it
    assert server.review_state(ch37, "final_37.mp4")["superseded"] is True
    assert server._approved_export(ch37) is None                          # not posted, not merged as approved


def test_a_verdict_given_before_this_check_existed_is_judged_as_before(studio):
    server, ch37, ch38, merged = studio
    server.save_reviews(ch37, {"final_37.mp4": {"status": "approved", "cut_signature": server.cut_signature(pdir=ch37)}})
    _edit_line(ch37, 1, "He flew into the sky.")
    assert server.review_state(ch37, "final_37.mp4")["superseded"] is False


def test_a_merged_video_is_out_of_date_when_one_of_its_chapters_changed(studio):
    server, ch37, ch38, merged = studio
    assert server._changed_pieces(merged) == []
    assert server.review_state(merged, "final_range_37-38.mp4")["superseded"] is False
    _edit_line(ch37, 1, "He flew into the sky.")                          # chapter 37 corrected after the merge
    rs = server.review_state(merged, "final_range_37-38.mp4")
    assert rs["changed_pieces"] == ["regressor_37"] and rs["superseded"] is True
    with pytest.raises(server.HTTPException) as e:
        server.api_review_save(server.ReviewIn(project="regressor_ch37-38", name="final_range_37-38.mp4",
                                               status="approved", notes="", allow_single=True))
    assert e.value.status_code == 409 and "Render the changed chapter again" in e.value.detail
    assert not os.path.exists(os.path.join(merged, server.REVIEWS_NAME))  # nothing recorded


def test_a_chapter_rendered_again_after_the_merge_also_counts(studio):
    server, ch37, ch38, merged = studio
    open(os.path.join(ch38, "exports", "final_38_new.mp4"), "wb").write(b"mp4")
    assert server._changed_pieces(merged) == ["regressor_38"]


def test_a_posted_merged_video_and_a_single_chapter_are_left_alone(studio):
    server, ch37, ch38, merged = studio
    _edit_line(ch37, 1, "He flew into the sky.")
    assert server._changed_pieces(ch37) == []
    json.dump({"youtube": {"id": "abc"}}, open(os.path.join(merged, server.UPLOADS_NAME), "w"))
    assert server._changed_pieces(merged) == []
