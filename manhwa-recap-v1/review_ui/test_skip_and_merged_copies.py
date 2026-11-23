"""Owner, 2026-11-23: "Merges aren't being done why? Why are there so many damn
reincarnations?" ... "Do all 3 and deploy."
  * Damn Reincarnation 85 and 91 have no source of their own (the pages repeat
    the chapter before). Marked skipped, they leave the writer's queue and merges
    step over them instead of stopping there.
  * A single chapter's own video and crops are a duplicate once a merge that
    holds it is posted, approved or queued (merged_copies); a piece of a merge
    still waiting for review stays (auto-append rebuilds from it)."""
import json
import os

import auto_append as aa
import autopilot
import hand_made
import retention


def _single(pid, status="review_pending", merged_into=None):
    return {"project": pid, "name": "final.mp4", "status": status, "merged_into": merged_into,
            "has_video": True, "superseded": False}


POSTED = [{"project": "dr_ch78-80", "name": "final_range_78-80.mp4", "start": 78, "end": 80, "at": 5}]
SINGLES = {n: _single(f"dr_{n}") for n in range(81, 95) if n not in (85, 91)}


def test_without_the_mark_the_run_stops_at_the_missing_chapter():
    d = aa.decide(POSTED, SINGLES, [], mode="cumulative")
    assert d["action"] == "build" and (d["start"], d["end"]) == (78, 84)


def test_cumulative_steps_over_skipped_chapters():
    d = aa.decide(POSTED, SINGLES, [], mode="cumulative", skip={85, 91})
    assert d["action"] == "build" and (d["start"], d["end"]) == (78, 94)
    assert "dr_85" not in d["projects"] and "dr_91" not in d["projects"]
    assert d["projects"][0] == "dr_ch78-80" and d["projects"][-1] == "dr_94"


def test_fresh_steps_over_skipped_chapters():
    d = aa.decide(POSTED, SINGLES, [], mode="fresh", min_new=1, skip={85, 91})
    assert d["action"] == "build" and (d["start"], d["end"]) == (81, 94)


def test_a_run_never_ends_on_a_skipped_chapter():
    singles = {81: _single("dr_81"), 82: _single("dr_82")}
    d = aa.decide(POSTED, singles, [], mode="cumulative", skip={83, 84})
    assert (d["start"], d["end"]) == (78, 82)
    # an unmarked hole after a skipped one still ends the run
    singles = {81: _single("dr_81"), 83: _single("dr_83")}
    assert aa.decide(POSTED, singles, [], mode="cumulative", skip={82, 90})["end"] == 83
    singles = {81: _single("dr_81"), 84: _single("dr_84")}
    assert aa.decide(POSTED, singles, [], mode="cumulative", skip={82})["end"] == 81


def _prep(root, pid):
    d = os.path.join(root, pid)
    os.makedirs(d)
    hand_made.prep_save(d, {"url": "https://x/" + pid, "variant": "", "state": "ready", "at": 1.0})


def test_skip_mark_leaves_the_writer_queue_and_undo_brings_it_back(tmp_path):
    root = str(tmp_path)
    for pid in ("damn-reincarnation_85", "damn-reincarnation_86"):
        _prep(root, pid)
    st = autopilot.load(root)
    st["ledger"]["dr|85"] = {"series_id": "damn-reincarnation", "chapter": "85", "project": "damn-reincarnation_85",
                             "source": "autopilot", "status": autopilot.WRITER}
    autopilot.save(root, st)
    try:
        autopilot.skip_chapter(root, "damn-reincarnation_85", "")
        raise AssertionError("a reason is required")
    except ValueError:
        pass
    autopilot.skip_chapter(root, "damn-reincarnation_85", "pages repeat ch.84")
    assert autopilot.skipped_chapters(root, "damn-reincarnation") == {85}
    assert autopilot.skipped_chapters(root, "other-series") == set()
    st = autopilot.load(root)
    assert st["ledger"]["dr|85"]["status"] == autopilot.SKIPPED
    assert autopilot.writer_waiting(st["ledger"]) == []
    q = hand_made.queue(root, skipped=autopilot.skipped_projects(root))
    assert [r["chapter_id"] for r in q["waiting"]] == ["damn-reincarnation_86"]

    autopilot.skip_chapter(root, "damn-reincarnation_85", "", undo=True)
    assert autopilot.skipped_chapters(root, "damn-reincarnation") == set()
    assert autopilot.load(root)["ledger"]["dr|85"]["status"] == autopilot.WRITER
    q = hand_made.queue(root, skipped=autopilot.skipped_projects(root))
    assert len(q["waiting"]) == 2


def test_skipped_chapter_is_not_picked_again():
    st = {"settings": {**autopilot.DEFAULTS}, "start_from": {"dr": "84"},
          "ledger": {"dr|85": {"series_id": "dr", "status": autopilot.SKIPPED, "project": "dr_85"}}}
    view = [{"id": "dr", "title": "DR", "best_mirror": "dr",
             "mirrors": [{"series_key": "dr", "support": "supported", "status": "ok",
                          "chapters": ["84", "85", "86"]}], "ingested": ["84"]}]
    row = autopilot.candidates(st, view, lambda k: k, now=10 ** 10)[0]
    assert row["remaining"] == ["86"] and row["next"] == "86"


# ------------------------------------------------------------ merged_copies

def _proj(root, pid, meta, exports=(), crops=True, reviews=None, publishes=None):
    d = os.path.join(root, pid)
    os.makedirs(os.path.join(d, "exports"))
    json.dump(meta, open(os.path.join(d, "project.json"), "w"))
    for n in exports:
        open(os.path.join(d, "exports", n), "wb").write(b"v" * 100)
    for sub in (("crops", "pages") if crops else ()):
        os.makedirs(os.path.join(d, sub))
        open(os.path.join(d, sub, "a.png"), "wb").write(b"c" * 10)
    open(os.path.join(d, "script.txt"), "w").write("story")
    if reviews:
        json.dump(reviews, open(os.path.join(d, "reviews.json"), "w"))
    if publishes:
        json.dump(publishes, open(os.path.join(d, "publishes.json"), "w"))
    return d


def _rng(a, b, pieces):
    return {"kind": "range", "chapters_start": a, "chapters_end": b,
            "merged_from": [{"project": p, "name": "final.mp4"} for p in pieces]}


def test_merged_copies_only_where_the_merge_will_not_be_rebuilt(tmp_path):
    root = str(tmp_path)
    # queued 78-80 (built from the singles), waiting 81-84 (its pieces stay)
    for n in range(78, 85):
        _proj(root, f"dr_{n}", {"chapter": n}, exports=["final.mp4"],
              reviews={"final.mp4": {"status": "merged"}})
    _proj(root, "dr_ch78-80", _rng(78, 80, ["dr_78", "dr_79", "dr_80"]), exports=["final_range_78-80.mp4"], crops=False)
    _proj(root, "dr_ch81-84", _rng(81, 84, [f"dr_{n}" for n in range(81, 85)]),
          exports=["final_range_81-84.mp4"], crops=False)
    # a chapter inside a posted merge
    _proj(root, "fv_368", {"chapter": 368}, exports=["final.mp4"])
    _proj(root, "fv_ch361-370", _rng(361, 370, ["fv_ch361-367", "fv_368"]), exports=["final_range_361-370.mp4"],
          crops=False, publishes={"final_range_361-370.mp4": {"results": [{"status": "published", "published_at": 5}]}})
    queued = [("dr_ch78-80", "final_range_78-80.mp4")]
    p = retention.plan(root, now=10, queued=queued)
    mc = sorted(c["path"] for c in p["candidates"] if c["category"] == "merged_copies")
    assert mc == sorted([f"dr_{n}/{x}" for n in (78, 79, 80) for x in ("crops", "exports/final.mp4")] +
                        ["fv_368/crops", "fv_368/exports/final.mp4"])
    kept = {k["path"] for k in p["kept"]}
    assert "dr_81/exports/final.mp4" in kept and "dr_78/pages" in kept
    assert all(not c["path"].endswith("script.txt") for c in p["candidates"])

    out = retention.apply(root, ["merged_copies"], now=10, queued=queued)
    assert len(out["items"]) == 8
    assert not os.path.exists(os.path.join(root, "dr_78", "exports", "final.mp4"))
    assert os.path.exists(os.path.join(root, "dr_78", "script.txt"))
    assert os.path.exists(os.path.join(root, "dr_78", "pages", "a.png"))
    assert os.path.exists(os.path.join(root, "dr_81", "exports", "final.mp4"))
    assert os.path.exists(os.path.join(root, "dr_ch78-80", "exports", "final_range_78-80.mp4"))


def test_an_approved_single_is_not_a_merged_copy(tmp_path):
    root = str(tmp_path)
    _proj(root, "s_5", {"chapter": 5}, exports=["final.mp4"], reviews={"final.mp4": {"status": "approved"}})
    _proj(root, "s_ch4-6", _rng(4, 6, ["s_4", "s_5", "s_6"]), exports=["final_range_4-6.mp4"], crops=False,
          reviews={"final_range_4-6.mp4": {"status": "approved"}})
    p = retention.plan(root, now=10)
    assert not [c for c in p["candidates"] if c["category"] == "merged_copies"]
