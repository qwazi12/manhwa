"""Invariant tests for storyboard_edit (editor v2). Builds a synthetic
project (3 narrated segments + real ffmpeg-generated MP3s) and drives every
op, asserting the timeline stays contiguous, audio-safe, and reversible.

Run: python3 test_storyboard_edit.py   (prints PASS/FAIL table, exits 1 on fail)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import storyboard_edit as se

RESULTS = []


def check(name, cond, note=""):
    RESULTS.append((name, bool(cond), note))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({note})" if note else ""))


def tone(path, secs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={secs}", "-q:a", "6", path],
                   check=True, capture_output=True)


def contiguous(segs):
    t = 0.0
    for s in segs:
        if abs(s["start"] - t) > 0.005:
            return False
        for b in s["beats"]:
            if b["start"] < s["start"] - 0.005 or b["end"] > s["start"] + s["dur"] + 0.005:
                return False
        t = round(t + s["dur"], 3)
    return True


def total(segs):
    return round(sum(s["dur"] for s in segs), 3)


def main():
    pdir = tempfile.mkdtemp(prefix="sbtest_")
    os.makedirs(os.path.join(pdir, "crops"))
    os.makedirs(os.path.join(pdir, "clips"))
    adir = os.path.join(pdir, "audio")
    # 3 beats of 2.0s / 3.0s / 2.5s
    for i, d in enumerate([2.0, 3.0, 2.5]):
        tone(os.path.join(adir, f"beat_{i:03d}.mp3"), d)
    for pid in ["p1", "p2", "p3", "p4", "p5"]:
        open(os.path.join(pdir, "crops", f"{pid}.png"), "wb").write(b"png")
    segs = [
        {"seg_index": 0, "panel_id": "p1", "panel_file": "", "start": 0.0,
         "dur": 2.35, "end": 2.35, "clip": "clips/seg_000.mp4",
         "beats": [{"index": 0, "text": "one", "start": 0.0, "end": 2.0}]},
        {"seg_index": 1, "panel_id": "p2", "panel_file": "", "start": 2.35,
         "dur": 3.35, "end": 5.7, "clip": "clips/seg_001.mp4",
         "beats": [{"index": 1, "text": "two", "start": 2.35, "end": 5.35}]},
        {"seg_index": 2, "panel_id": "p3", "panel_file": "", "start": 5.7,
         "dur": 2.85, "end": 8.55, "clip": "clips/seg_002.mp4",
         "beats": [{"index": 2, "text": "three", "start": 5.7, "end": 8.2}]},
    ]
    json.dump(segs, open(os.path.join(pdir, "segments.json"), "w"))
    descs = [{"panel_id": p} for p in ["p1", "p2", "p3", "p4", "p5"]]
    scenes = [{"scene_id": 1, "panel_ids": ["p1", "p2", "p4"], "text": "unit one"},
              {"scene_id": 2, "panel_ids": ["p3"], "text": "unit two"}]
    base_total = total(segs)

    # P2: set_duration extends with silence, ripples, conserves others
    s = se.set_duration(pdir, 0, 5.0)
    check("set_duration extends", abs(s[0]["dur"] - 5.0) < 0.01 and contiguous(s))
    check("set_duration ripples total", abs(total(s) - (base_total + 5.0 - 2.35)) < 0.01)
    try:
        se.set_duration(pdir, 0, 1.0)
        check("set_duration audio floor", False, "should have raised")
    except ValueError:
        check("set_duration audio floor", True)
    se.set_duration(pdir, 0, 2.35)   # restore

    # P2: move_boundary conserves total; mid-beat cut slices audio
    before = total(se.load(pdir))
    s = se.move_boundary(pdir, 1, -1.0)   # seg1 shrinks 1s: cut lands mid-beat 1
    sliced = [b for x in s for b in x["beats"] if b.get("file")]
    check("boundary conserves total", abs(total(s) - before) < 0.01 and contiguous(s))
    check("mid-beat cut slices audio", len(sliced) == 2,
          f"{len(sliced)} slice parts")
    part_files = [os.path.join(pdir, "audio", b["file"]) for b in sliced]
    check("slice files exist", all(os.path.exists(p) for p in part_files))
    if len(part_files) == 2:
        d0, d1 = se._ffdur(part_files[0]), se._ffdur(part_files[1])
        check("slice durations sum to beat", abs((d0 + d1) - 3.0) < 0.15,
              f"{d0}+{d1}")
    s = se.move_boundary(pdir, 1, 1.0)    # move back

    # V3: a host too short to leave MIN_SEG_DUR on both sides must REFUSE
    # with a clear message rather than mint a sub-second flicker segment.
    try:
        se.include_panel(pdir, "p4", scenes, descs)
        check("short host refused (V3 floor)", False, "no error raised")
    except ValueError as e:
        check("short host refused (V3 floor)", "no split point" in str(e), str(e)[:60])

    # P3: include folded panel carves its unit host, conserves total. Give the
    # host room to satisfy the floor first (2s each side + the carve).
    segs_now = se.load(pdir)
    se.set_duration(pdir, segs_now[1]["seg_index"], 8.0)
    before = total(se.load(pdir))
    s = se.include_panel(pdir, "p4", scenes, descs)
    check("include carve conserves total", abs(total(s) - before) < 0.01 and contiguous(s))
    check("include panel on timeline", any(x["panel_id"] == "p4" for x in s))
    check("carve respects min duration",
          all(x["dur"] >= se.MIN_SEG_DUR - 0.01 for x in s if not x.get("silent_hold")),
          f"min={min(x['dur'] for x in s):.2f}")

    # P3 reverse: exclude folds back
    s = se.exclude_panel(pdir, "p4")
    check("exclude restores total", abs(total(s) - before) < 0.01 and contiguous(s))
    check("exclude removed panel", not any(x["panel_id"] == "p4" for x in s))

    # P4: junk panel silent hold extends runtime by exactly `hold`
    before = total(se.load(pdir))
    s = se.include_panel(pdir, "p5", scenes, descs, hold=3.0)
    hold_seg = next(x for x in s if x["panel_id"] == "p5")
    check("silent hold extends runtime", abs(total(s) - (before + 3.0)) < 0.01)
    check("silent hold has no beats", hold_seg["beats"] == [] and contiguous(s))
    s = se.exclude_panel(pdir, "p5")
    check("silent hold removal restores", abs(total(s) - before) < 0.01)

    # P5: reorder keeps totals + contiguity, moves the segment
    s = se.reorder(pdir, 2, 0)
    check("reorder moves seg", s[0]["seg_index"] == 2 and contiguous(s))
    check("reorder conserves total", abs(total(s) - before) < 0.01)
    s = se.reorder(pdir, 2, 2)

    # P6: add_line synthesizes + grows the segment
    def fake_synth(text, out):
        tone(out, 1.5)
    before = total(se.load(pdir))
    s = se.add_line(pdir, 0, "a brand new sentence", fake_synth)
    seg0 = next(x for x in s if x["seg_index"] == 0)
    check("add_line appends beat", len(seg0["beats"]) == 2 and contiguous(s))
    check("add_line grows window", total(s) > before)

    # P7: coalesce after slicing merges parts back
    se.move_boundary(pdir, 1, -1.0)
    owner = se.coalesce_beat(pdir, 1)
    s = se.load(pdir)
    parts = [b for x in s for b in x["beats"] if b["index"] == 1]
    check("coalesce single whole beat", len(parts) == 1 and not parts[0].get("file"),
          f"owner seg {owner}")

    # stale-clip marking: touched clips were deleted
    open(os.path.join(pdir, "clips", "seg_000.mp4"), "wb").write(b"x")
    se.set_duration(pdir, 0, 9.0)
    check("stale clip deleted", not os.path.exists(os.path.join(pdir, "clips", "seg_000.mp4")))

    # Narration Group Timing Model Tests (Scenario: 4 images / 12.0s narration)
    g_pdir = tempfile.mkdtemp(prefix="sbgtest_")
    os.makedirs(os.path.join(g_pdir, "crops"))
    os.makedirs(os.path.join(g_pdir, "clips"))
    g_adir = os.path.join(g_pdir, "audio")
    tone(os.path.join(g_adir, "beat_000.mp3"), 12.0)
    for pid in ["g1", "g2", "g3", "g4", "g5"]:
        open(os.path.join(g_pdir, "crops", f"{pid}.png"), "wb").write(b"png")
    # Group 1: 4 images sharing 12.0s narration (scene_id: 1)
    # Group 2: 1 image of 5.0s narration (scene_id: 2)
    g_segs = [
        {"seg_index": 10, "panel_id": "g1", "scene_id": 1, "start": 0.0, "dur": 3.0, "end": 3.0, "group_dur": 12.0, "clip": "clips/seg_010.mp4", "beats": [{"index": 0, "text": "group1", "start": 0.0, "end": 12.0}]},
        {"seg_index": 11, "panel_id": "g2", "scene_id": 1, "start": 3.0, "dur": 3.0, "end": 6.0, "group_dur": 12.0, "clip": "clips/seg_011.mp4", "beats": [{"index": 0, "text": "group1", "start": 0.0, "end": 12.0}]},
        {"seg_index": 12, "panel_id": "g3", "scene_id": 1, "start": 6.0, "dur": 3.0, "end": 9.0, "group_dur": 12.0, "clip": "clips/seg_012.mp4", "beats": [{"index": 0, "text": "group1", "start": 0.0, "end": 12.0}]},
        {"seg_index": 13, "panel_id": "g4", "scene_id": 1, "start": 9.0, "dur": 3.0, "end": 12.0, "group_dur": 12.0, "clip": "clips/seg_013.mp4", "beats": [{"index": 0, "text": "group1", "start": 0.0, "end": 12.0}]},
        {"seg_index": 14, "panel_id": "g5", "scene_id": 2, "start": 12.0, "dur": 5.0, "end": 17.0, "group_dur": 5.0, "clip": "clips/seg_014.mp4", "beats": [{"index": 1, "text": "group2", "start": 12.0, "end": 17.0}]},
    ]
    json.dump(g_segs, open(os.path.join(g_pdir, "segments.json"), "w"))
    g_descs = [{"panel_id": p} for p in ["g1", "g2", "g3", "g4", "g5"]]
    g_scenes = [
        {"scene_id": 1, "panel_ids": ["g1", "g2", "g3", "g4"], "text": "narration group 1"},
        {"scene_id": 2, "panel_ids": ["g5"], "text": "narration group 2"}
    ]

    # Test A: 4 images default equal allocation = 3.0s each, sum = 12.0s
    gs = se.load(g_pdir)
    g1_durs = [s["dur"] for s in gs[:4]]
    check("narration group default 4 images equal division", g1_durs == [3.0, 3.0, 3.0, 3.0] and total(gs[:4]) == 12.0)

    # Test B: Manual override of image 3 to 6.0s -> auto siblings rebalance to 2.0s / 2.0s / 6.0s / 2.0s, total = 12.0s
    gs = se.set_duration(g_pdir, 12, 6.0)
    g1_durs_after = [s["dur"] for s in gs[:4]]
    check("manual duration edit rebalances siblings inside group", g1_durs_after == [2.0, 2.0, 6.0, 2.0], f"durs={g1_durs_after}")
    check("narration group total conserved", total(gs[:4]) == 12.0)
    check("no global ripple to subsequent groups", gs[4]["start"] == 12.0 and total(gs) == 17.0)

    # Test C: Validation error when manual edit leaves siblings below MIN_SEG_DUR
    try:
        se.set_duration(g_pdir, 12, 11.0)
        check("group rebalance validation error below floor", False, "should have raised")
    except ValueError as e:
        check("group rebalance validation error below floor", "3 sibling image(s)" in str(e), str(e)[:70])

    # Test D: Exclude image g4 -> remaining 3 images absorb freed time within 12.0s
    gs = se.exclude_panel(g_pdir, "g4")
    g1_rem_durs = [s["dur"] for s in gs if s.get("scene_id") == 1]
    check("exclude image rebalances remaining group siblings", total(gs[:3]) == 12.0 and len(g1_rem_durs) == 3, f"durs={g1_rem_durs}")
    check("global timeline after exclude conserved", gs[-1]["start"] == 12.0 and total(gs) == 17.0)

    shutil.rmtree(g_pdir)

    shutil.rmtree(pdir)
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

