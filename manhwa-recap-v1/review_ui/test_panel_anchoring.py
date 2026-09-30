#!/usr/bin/env python3
"""Test panel-anchored narration parsing, tag stripping, and beat segmentation."""

import os
import sys

DEV_ROOT = "/Users/kwasiyeboah/dev/manhwa/manhwa-recap-v1"
sys.path.insert(0, DEV_ROOT)
sys.path.insert(0, os.path.join(DEV_ROOT, "review_ui"))

import narrate
import beat_segmenter

passed = 0


def check(name, cond):
    global passed
    assert cond, f"FAIL: {name}"
    print(f"PASS {name}")
    passed += 1


def run_tests():
    panels = [{"panel_id": f"page001_panel_{i:03d}"} for i in range(1, 6)]
    pids = [p["panel_id"] for p in panels]

    # 1. Structured tags parsing
    raw_sample = """
[ID: page001_panel_001] Ash took off into the dark forest at a dead sprint.
[ID: page001_panel_002, page001_panel_003] Slashing outward with sudden fury, he shattered the incoming daggers into sparks.
[ID: page001_panel_004] (silent)
[ID: page001_panel_005] "Drop your weapons!" the captain roared, closing the distance.
"""
    items = narrate.parse_anchored_narrations(raw_sample, panels)
    check("parse: all 5 panels accounted for", len(items) == 4)
    check("parse: single tag maps to panel 1", items[0]["panel_ids"] == ["page001_panel_001"])
    check("parse: action pair maps to panels 2 and 3", items[1]["panel_ids"] == ["page001_panel_002", "page001_panel_003"])
    check("parse: silent tag marked as silent", items[2]["silent"] is True and items[2]["text"] == "")
    check("parse: dialogue line preserved", "Drop your weapons!" in items[3]["text"])

    # 2. Missing panel in model output auto-fills as silent hold
    partial_sample = """
[ID: page001_panel_001] Ash ran into the darkness.
[ID: page001_panel_002] He stumbled over a root.
"""
    items_partial = narrate.parse_anchored_narrations(partial_sample, panels)
    covered_ids = [pid for it in items_partial for pid in it["panel_ids"]]
    check("partial: all 5 panels covered in output", set(covered_ids) == set(pids))
    check("partial: missing panels marked silent", sum(1 for it in items_partial if it["silent"]) == 3)

    # 3. Untagged prose fallback
    prose_sample = "Ash ran into the dark night. A heavy branch struck his shoulder. The cruel pursuers laughed in mockery."
    items_prose = narrate.parse_anchored_narrations(prose_sample, panels)
    covered_prose_ids = [pid for it in items_prose for pid in it["panel_ids"]]
    check("fallback: all 5 panels covered in fallback", set(covered_prose_ids) == set(pids))
    check("fallback: first 3 panels get sentences", items_prose[0]["text"] != "" and items_prose[2]["text"] != "")

    # 4. strip_anchored_tags
    clean_txt = narrate.strip_anchored_tags(raw_sample)
    check("strip: no [ID:] tags remain in script text", "[ID:" not in clean_txt)
    check("strip: (silent) is omitted from script text", "(silent)" not in clean_txt)
    check("strip: sentences preserved", "Ash took off" in clean_txt and "Drop your weapons!" in clean_txt)

    # 5. segment_beats_scenes
    scenes = narrate.provenance([ (panels, raw_sample) ])
    beats = beat_segmenter.segment_beats_scenes(scenes)
    check("beats: beats produced", len(beats) >= 4)
    check("beats: silent beat carries silent=True", any(b.get("silent") for b in beats))
    check("beats: panel_ids carried on every beat", all(len(b.get("panel_ids", [])) > 0 for b in beats))

    print(f"\n{passed}/{passed} passed")


if __name__ == "__main__":
    run_tests()
