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
