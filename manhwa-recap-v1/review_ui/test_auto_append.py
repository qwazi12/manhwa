"""Auto-append. Owner 2026-11-08: the next merge starts after the last posted video (no overlaps)."""
import auto_append as aa


def _single(pid, status="review_pending", merged_into=None, has_video=True, superseded=False):
    return {"project": pid, "name": "final.mp4", "status": status, "merged_into": merged_into,
            "has_video": has_video, "superseded": superseded}


POSTED = [{"project": "s_ch20-30", "name": "final_range_20-30.mp4", "start": 20, "end": 30, "at": 5}]


def test_owner_rule_20261108_next_merge_starts_after_the_posted_video():
    d = aa.decide(POSTED, {31: _single("s_31"), 32: _single("s_32")}, [])
    assert d["action"] == "build"
    assert (d["start"], d["end"]) == (31, 32)
    assert d["projects"] == ["s_31", "s_32"]          # the posted 20-30 is not re-used
    assert d["base"]["project"] == "s_ch20-30" and d["retire"] == []


def test_waits_for_enough_new_chapters():
    singles = {n: _single(f"s_{n}") for n in range(31, 37)}
    d = aa.decide(POSTED, singles, [], min_new=7)
    assert d["action"] == "wait" and "6 new chapter(s) after 30" in d["why"] and "starts at 31" in d["why"]
    singles[37] = _single("s_37")
    d = aa.decide(POSTED, singles, [], min_new=7)
    assert d["action"] == "build" and (d["start"], d["end"]) == (31, 37)


def test_nothing_posted_or_nothing_new():
    assert aa.decide([], {31: _single("s_31")}, [])["action"] == "none"
    assert aa.decide(POSTED, {}, [])["action"] == "none"


def test_overlapping_waiting_merge_is_recut_from_the_next_chapter():
    # Death Knight: posted 88-94, waiting 88-95 (overlaps); 96..101 render -> 95-101 replaces 88-95
    posted = [{"project": "dk_ch88-94", "name": "a", "start": 88, "end": 94, "at": 1}]
    pend = [{"project": "dk_ch88-95", "start": 88, "end": 95, "building": False, "approved": False,
             "pieces": [{"project": "dk_ch88-94"}, {"project": "dk_95"}]}]
    singles = {95: _single("dk_95", "merged", "dk_ch88-95")}
    d = aa.decide(posted, singles, pend, min_new=7)
    assert d["action"] == "wait" and "starts at 95" in d["why"]
    singles.update({n: _single(f"dk_{n}") for n in range(96, 102)})
    d = aa.decide(posted, singles, pend, min_new=7)
    assert d["action"] == "build" and (d["start"], d["end"]) == (95, 101)
    assert d["projects"] == [f"dk_{n}" for n in range(95, 102)] and d["retire"] == ["dk_ch88-95"]


def test_waiting_merge_grows_until_approved():
    pend = [{"project": "s_ch31-32", "start": 31, "end": 32, "building": False, "approved": False, "pieces": []}]
    singles = {31: _single("s_31", "merged", "s_ch31-32"), 32: _single("s_32", "merged", "s_ch31-32"),
               33: _single("s_33")}
    d = aa.decide(POSTED, singles, pend)
    assert d["action"] == "build" and (d["start"], d["end"]) == (31, 33) and d["retire"] == ["s_ch31-32"]


def test_already_covered_is_a_no_op():
    pend = [{"project": "s_ch31-32", "start": 31, "end": 32, "building": False, "approved": False, "pieces": []}]
    singles = {31: _single("s_31", "merged", "s_ch31-32"), 32: _single("s_32", "merged", "s_ch31-32")}
    assert aa.decide(POSTED, singles, pend)["action"] == "none"


def test_approved_merge_is_never_replaced():
    pend = [{"project": "s_ch31-31", "start": 31, "end": 31, "building": False, "approved": True, "pieces": []}]
    singles = {31: _single("s_31", "merged", "s_ch31-31"), 32: _single("s_32")}
    d = aa.decide(POSTED, singles, pend)
    assert d["action"] == "wait" and "approved" in d["why"]


def test_building_waits():
    pend = [{"project": "s_ch31-31", "start": 31, "end": 31, "building": True, "approved": False, "pieces": []}]
    assert aa.decide(POSTED, {31: _single("s_31")}, pend)["action"] == "wait"


def test_run_stops_at_gap_unrendered_approved_or_edited_chapter():
    assert aa.decide(POSTED, {32: _single("s_32")}, [])["action"] == "none"           # 31 missing
    assert aa.decide(POSTED, {31: _single("s_31", has_video=False)}, [])["action"] == "none"
    assert aa.decide(POSTED, {31: _single("s_31", "approved")}, [])["action"] == "none"
    assert aa.decide(POSTED, {31: _single("s_31", superseded=True)}, [])["action"] == "none"
    d = aa.decide(POSTED, {31: _single("s_31"), 32: _single("s_32", "approved")}, [])
    assert d["end"] == 31


def test_base_is_the_posted_video_reaching_furthest():
    posted = POSTED + [{"project": "s_31", "name": "z", "start": 31, "end": 31, "at": 9}]
    d = aa.decide(posted, {32: _single("s_32")}, [])
    assert d["projects"] == ["s_32"] and (d["start"], d["end"]) == (32, 32) and d["base"]["project"] == "s_31"


def test_relabel_title():
    assert aa.relabel_title("[20-30] Big Hook | Flamingo Recap", 20, 32) == "[20-32] Big Hook | Flamingo Recap"
    assert aa.relabel_title("[48] Hook — Murim", 48, 49) == "[48-49] Hook — Murim"
    assert aa.relabel_title("No bracket", 1, 2) == "[1-2] No bracket"


def test_merge_tags_moves_the_posted_range_and_drops_other_numbers():
    tags = aa.merge_tags(["series chapter 20-32"],
                         ["series chapter 20-30", "series chapters 20 to 30", "series chapter 7", "manhwa recap"],
                         20, 32, posted_label="20-30")
    assert "series chapter 20-32" in tags
    assert "series chapters 20 to 32" in tags
    assert "series chapter 7" not in tags and "manhwa recap" in tags
    assert len(tags) == len(set(t.lower() for t in tags))


def test_timestamps_replace_old_block_in_posted_style():
    desc = "Hook line.\n\nTimestamps:\n00:00 Chapter 20\n\n📖 Series: S\n\n❤️ Support"
    out = aa.with_timestamps(desc, [(20, 0.0), (21, 600.5), (22, 3725)])
    assert "00:00 Chapter 20" not in out
    assert "⏱️ Timestamps:\n• 0:00 - Chapter 20\n• 10:00 - Chapter 21\n• 1:02:05 - Chapter 22\n\n❤️ Support" in out
    assert out.startswith("Hook line.\n\n📖 Series: S")


# ---- cumulative mode (owner brief 2026-11-13; the 2026-11-04 rule restored)

def _cp(a, b, pid=None):
    return {"project": pid or f"s_ch{a}-{b}", "name": "v.mp4", "start": a, "end": b, "at": 1}


def _cs(n, status="review_pending", **kw):
    return {"project": f"s_{n}", "name": "final.mp4", "status": status, "superseded": False,
            "merged_into": None, "has_video": True, **kw}


def test_cumulative_old_compilation_plus_one_new_chapter():
    """Review 2026-11-13: with the threshold at 1 the fresh rule picked only
    chapter 5; the cumulative rule gives 1-5 with the posted video inside."""
    posted, singles = [_cp(1, 4)], {5: _cs(5)}
    fresh = aa.decide(posted, singles, [], min_new=1, mode="fresh")
    assert (fresh["start"], fresh["end"], fresh["projects"]) == (5, 5, ["s_5"])
    d = aa.decide(posted, singles, [], min_new=1, mode="cumulative")
    assert d["action"] == "build" and (d["start"], d["end"]) == (1, 5)
    assert d["projects"] == ["s_ch1-4", "s_5"] and d["base"]["project"] == "s_ch1-4" and d["retire"] == []


def test_cumulative_chain_each_release_extends_the_last():
    d = aa.decide([_cp(1, 4), _cp(1, 5)], {6: _cs(6), 7: _cs(7)}, [], mode="cumulative")
    assert (d["start"], d["end"], d["projects"]) == (1, 7, ["s_ch1-5", "s_6", "s_7"])


def test_cumulative_replaces_a_waiting_merge_but_never_an_approved_one():
    posted = [_cp(1, 4)]
    singles = {5: _cs(5, status="merged", merged_into="s_ch1-5"), 6: _cs(6)}
    waiting = {"project": "s_ch1-5", "start": 1, "end": 5, "building": False, "approved": False,
               "pieces": [{"project": "s_ch1-4", "name": "v.mp4"}, {"project": "s_5", "name": "final.mp4"}]}
    d = aa.decide(posted, singles, [waiting], mode="cumulative")
    assert (d["start"], d["end"]) == (1, 6) and d["retire"] == ["s_ch1-5"]
    assert d["projects"] == ["s_ch1-4", "s_5", "s_6"]
    held = aa.decide(posted, singles, [dict(waiting, approved=True)], mode="cumulative")
    assert held["action"] == "wait" and "ch 6 will be added" in held["why"]


def test_cumulative_waits_for_min_new_and_for_a_build_in_progress():
    posted = [_cp(1, 4)]
    w = aa.decide(posted, {5: _cs(5)}, [], min_new=2, mode="cumulative")
    assert w["action"] == "wait" and "once 2 are ready" in w["why"]
    b = aa.decide(posted, {5: _cs(5)}, [{"project": "x", "start": 1, "end": 5, "building": True,
                                             "approved": False, "pieces": []}], mode="cumulative")
    assert b["action"] == "wait"
    assert aa.decide(posted, {5: _cs(5, status="approved")}, [], mode="cumulative")["action"] == "none"
    assert aa.decide(posted, {6: _cs(6)}, [], mode="cumulative")["action"] == "none"     # 5 is missing: no gap


def test_unknown_mode_raises():
    import pytest
    with pytest.raises(ValueError):
        aa.decide([_cp(1, 4)], {}, [], mode="both")
