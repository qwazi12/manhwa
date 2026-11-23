"""Owner, 2026-11-23: every rendered chapter joins its series' waiting merge —
one waiting video per series, even before anything of the series has posted."""
import auto_append as aa


def _single(n, status="review_pending", merged_into=None, sid="dr"):
    return {"project": f"{sid}_{n}", "name": f"final_{n}.mp4", "status": status, "superseded": False,
            "merged_into": merged_into, "has_video": True}


def _merge(a, b, approved=False, pieces=None, sid="dr", building=False):
    pieces = pieces if pieces is not None else [f"{sid}_{n}" for n in range(a, b + 1)]
    return {"project": f"{sid}_ch{a}-{b}", "name": f"final_range_{a}-{b}.mp4", "start": a, "end": b,
            "building": building, "approved": approved, "pieces": [{"project": p} for p in pieces]}


def _dr():
    pending = [_merge(78, 80, approved=True),
               _merge(81, 90, pieces=[f"dr_{n}" for n in range(81, 91) if n != 85]),
               _merge(92, 101)]
    singles = {n: _single(n, "merged", "dr_ch81-90") for n in range(81, 91) if n != 85}
    singles.update({n: _single(n, "merged", "dr_ch92-101") for n in range(92, 102)})
    return pending, singles


def test_unposted_series_is_folded_into_one_video_after_the_approved_one():
    pending, singles = _dr()
    for mode in aa.MODES:
        d = aa.decide([], singles, pending, min_new=1, mode=mode, skip={85, 91})
        assert d["action"] == "build", d
        assert (d["start"], d["end"]) == (78, 101)
        want = [f"dr_{n}" for n in range(78, 102) if n not in (85, 91)]
        assert d["projects"] == want
        assert d["retire"] == ["dr_ch81-90", "dr_ch92-101"]      # the approved 78-80 stays queued
        assert d["extends"] == ["dr_ch78-80"]
        assert d["base"]["project"] == "dr_ch78-80"


def test_longer_video_already_waiting_is_not_built_twice():
    pending, singles = _dr()
    pending.append(_merge(78, 101, pieces=[f"dr_{n}" for n in range(78, 102) if n not in (85, 91)]))
    for n in singles:
        singles[n] = _single(n, "merged", "dr_ch78-101")
    d = aa.decide([], singles, pending, min_new=1, mode="cumulative", skip={85, 91})
    assert d["action"] == "none" and "dr_ch78-101 already has chapters 78-101" in d["why"]
    assert "place" in d["why"]


def test_queued_merge_of_a_posted_series_gets_the_next_chapter_before_it_posts():
    posted = [{"project": "h_ch85-91", "name": "final_range_85-91.mp4", "start": 85, "end": 91, "at": 1}]
    pending = [_merge(85, 93, approved=True, pieces=["h_ch85-91", "h_92", "h_93"], sid="h")]
    singles = {92: _single(92, "merged", "h_ch85-93", "h"), 93: _single(93, "merged", "h_ch85-93", "h"),
               94: _single(94, sid="h")}
    d = aa.decide(posted, singles, pending, min_new=1, mode="cumulative")
    assert d["action"] == "build", d
    assert (d["start"], d["end"]) == (85, 94)
    assert d["projects"] == ["h_ch85-91", "h_92", "h_93", "h_94"]
    assert d["retire"] == [] and d["extends"] == ["h_ch85-93"]


def test_unapproved_waiting_merge_is_replaced_by_the_longer_one():
    pending = [_merge(98, 101, sid="b")]
    singles = {n: _single(n, "merged", "b_ch98-101", "b") for n in range(98, 102)}
    singles.update({102: _single(102, sid="b"), 103: _single(103, sid="b")})
    d = aa.decide([], singles, pending, min_new=1, mode="cumulative")
    assert d["action"] == "build" and (d["start"], d["end"]) == (98, 103)
    assert d["retire"] == ["b_ch98-101"] and d["extends"] == []


def test_a_piece_of_an_approved_merge_is_never_retired():
    # Dark Mage: 111-112 (approved) was built from 111 + the 112-112 merge
    pending = [_merge(111, 112, approved=True, pieces=["m_111", "m_ch112-112"], sid="m"),
               _merge(112, 112, pieces=["m_112"], sid="m")]
    singles = {112: _single(112, "merged", "m_ch112-112", "m"), 113: _single(113, sid="m"),
               114: _single(114, sid="m")}
    posted = [{"project": "m_ch104-110", "name": "x.mp4", "start": 104, "end": 110, "at": 1}]
    d = aa.decide(posted, singles, pending, min_new=1, mode="cumulative")
    assert d["action"] == "build" and (d["start"], d["end"]) == (111, 114)
    assert "m_ch112-112" not in d["retire"]
    assert d["projects"] == ["m_111", "m_ch112-112", "m_113", "m_114"]


def test_waits_while_a_merge_of_the_series_is_building():
    pending, singles = _dr()
    pending.append(_merge(1, 1, building=True))
    d = aa.decide([], singles, pending, min_new=1, mode="cumulative", skip={85, 91})
    assert d["action"] == "wait" and "being built" in d["why"]


def test_nothing_waiting_and_nothing_posted_is_unchanged():
    d = aa.decide([], {5: _single(5)}, [], min_new=1, mode="cumulative")
    assert d == {"action": "none", "why": "nothing posted yet"}
