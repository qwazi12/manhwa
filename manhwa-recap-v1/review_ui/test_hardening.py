"""P0/P1/P3 + dimension-source tests (Session 25 hardening pass).

  P3 — crop validation and the reviewer's "use full panel" override.
  P0 — the timeline must not be renderable if audio falls outside its window.
  P1 — include_panel()'s carve used to swap two segments' window starts
       WITHOUT swapping their beats, so each half of a sliced sentence landed
       on the other's window and was cut off at render.
  dims — the real PNG is the only geometry source.

Run: python3 test_hardening.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "hyperframes"))

import render_segments as rs
import storyboard as sb
import storyboard_edit as se
from shot_planner import (CROP_MIN_AREA, crop_area, crop_status, effective_crop,
                          is_full_frame_crop, is_sub_crop)

FULL = [0.0, 0.0, 1.0, 1.0]
SUB = [0.09, 0.04, 0.96, 0.49]      # real Martial Genius seg 9  (39.2%)
TINY = [0.44, 0.31, 0.66, 0.42]     # real Swordmasters seg 71   (2.5%)


def _mp3(path, secs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"anullsrc=r=44100:cl=mono", "-t", f"{secs:.3f}",
                    "-q:a", "9", path], check=True, capture_output=True)


def _png(path, w, h):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={w}x{h}",
                    "-frames:v", "1", path], check=True, capture_output=True)


def _project(tmp):
    """Minimal on-disk project: one 10s host segment carrying one 9s beat."""
    os.makedirs(os.path.join(tmp, "crops"), exist_ok=True)
    _mp3(os.path.join(tmp, "audio", "beat_000.mp3"), 9.0)
    for pid, (w, h) in {"host": (600, 400), "newp": (600, 400)}.items():
        _png(os.path.join(tmp, "crops", f"{pid}.png"), w, h)
    segs = [{"seg_index": 0, "panel_id": "host",
             "panel_file": os.path.join(tmp, "crops", "host.png"),
             "start": 0.0, "end": 10.0, "dur": 10.0,
             "crop_bbox_norm": FULL, "width": 600, "height": 400,
             "clip": "clips/seg_000.mp4", "user_included": True,
             "beats": [{"index": 0, "text": "one long sentence",
                        "start": 0.0, "end": 9.0}]}]
    json.dump(segs, open(os.path.join(tmp, "segments.json"), "w"))
    return segs


def main():
    r = []
    tmp = tempfile.mkdtemp(prefix="harden_")
    _project(tmp)

    # ------------------------------------------------------------- P3 contract
    r.append(("crop_status classifies none/invalid/full/tiny/sub",
              [crop_status(b) for b in (None, [0.9, 0.9, 0.1, 0.1], FULL, TINY, SUB)]
              == ["none", "invalid", "full", "tiny", "sub"]))
    r.append(("a below-floor crop auto-falls back to the full panel",
              effective_crop(TINY) is None and crop_area(TINY) < CROP_MIN_AREA))
    r.append(("a usable sub-crop is still honoured", effective_crop(SUB) == SUB))
    r.append(("is_full_frame_crop is true only for the whole panel",
              is_full_frame_crop(FULL) and not is_full_frame_crop(SUB)))
    r.append(("P4 preserved: full-frame box is not a sub-crop",
              is_sub_crop(FULL) is False and is_sub_crop(SUB) is True))
    r.append(("badge percentage matches the box area",
              abs(crop_area(SUB) * 100 - 39.15) < 0.1))

    # ------------------------------------------- P3 reviewer override end-to-end
    segs = se.load(tmp)
    segs[0]["crop_bbox_norm"] = SUB
    se.save(tmp, segs)
    seg = se.load(tmp)[0]
    r.append(("before override the exporter crops",
              'id="card_container"' in rs.seg_html(dict(seg, dur=10.0), os.path.join(tmp, "audio"))))
    se.use_full_panel(tmp, 0)
    seg = se.load(tmp)[0]
    r.append(("use_full_panel writes a full-frame box to segments.json",
              seg["crop_bbox_norm"] == FULL))
    r.append(("use_full_panel keeps the planner box for audit/restore",
              seg.get("crop_bbox_norm_ai") == SUB and seg["focus_source"] == "manual_full"))
    r.append(("override persists across a reload", se.load(tmp)[0]["crop_bbox_norm"] == FULL))
    rs.ASSETS = os.path.join(tmp, "crops")
    r.append(("override reaches the EXPORT, not just the preview",
              'id="card_container"' not in rs.seg_html(dict(seg, dur=10.0),
                                                       os.path.join(tmp, "audio"))))
    se.restore_ai_crop(tmp, 0)
    r.append(("restore_crop puts the planner box back",
              se.load(tmp)[0]["crop_bbox_norm"] == SUB))
    se.use_full_panel(tmp, 0)      # leave it overridden for the timing tests

    # --------------------------------------------------------------- P0 timing
    v = se.validate_timeline(tmp)
    r.append(("a healthy timeline validates clean", v["ok"] and v["n_errors"] == 0))

    # mp3 re-encode padding must NOT be reported as truncation (live Martial
    # Genius slices came back +0.067s and wrongly blocked export at tol 0.06)
    segs = se.load(tmp)
    segs[0]["dur"] = 8.94; segs[0]["end"] = 8.94       # 9.0s audio, 0.06s over
    se.save(tmp, segs)
    r.append(("a few frames of encoder padding is tolerated, not 'truncated'",
              se.validate_timeline(tmp)["ok"]))
    segs = se.load(tmp)
    segs[0]["dur"] = 8.0; segs[0]["end"] = 8.0         # 9.0s audio, 1.0s over
    se.save(tmp, segs)
    r.append(("real truncation well past the padding is still caught",
              any(e["rule"] == "G2-truncated"
                  for e in se.validate_timeline(tmp)["errors"])))
    segs = se.load(tmp); segs[0]["dur"] = 10.0; segs[0]["end"] = 10.0
    se.save(tmp, segs)

    segs = se.load(tmp); segs[0]["dur"] = 5.0; segs[0]["end"] = 5.0; se.save(tmp, segs)
    v = se.validate_timeline(tmp)
    r.append(("audio longer than its window is REJECTED (G2)",
              not v["ok"] and any(e["rule"] == "G2-truncated" for e in v["errors"])))

    segs = se.load(tmp); segs[0]["dur"] = 10.0; segs[0]["end"] = 10.0
    segs[0]["beats"][0]["start"] = -3.0; segs[0]["beats"][0]["end"] = 6.0
    se.save(tmp, segs)
    v = se.validate_timeline(tmp)
    r.append(("audio starting before its window is REJECTED (G1)",
              any(e["rule"] == "G1-starts-before-window" for e in v["errors"])))

    segs = se.load(tmp); segs[0]["beats"][0]["start"] = 20.0
    segs[0]["beats"][0]["end"] = 29.0; se.save(tmp, segs)
    r.append(("audio wholly outside its window is REJECTED (G3)",
              any(e["rule"] == "G3-outside-window"
                  for e in se.validate_timeline(tmp)["errors"])))

    segs = se.load(tmp); segs[0]["beats"] = []; se.save(tmp, segs)
    v = se.validate_timeline(tmp)
    r.append(("an unmarked silent segment WARNS about dead air",
              any(w["rule"] == "G5-dead-air" for w in v["warnings"])))
    segs = se.load(tmp); segs[0]["silent_hold"] = True; se.save(tmp, segs)
    v = se.validate_timeline(tmp)
    r.append(("an explicit silent hold passes without warning",
              v["ok"] and not any(w["rule"] == "G5-dead-air" for w in v["warnings"])))

    # G7: repeated carving leaves two overlapping slices of one sentence in
    # a single segment; G6 misses it because the filenames differ.
    segs = se.load(tmp)
    segs[0]["beats"] = [
        {"index": 0, "text": "x", "start": 0.0, "end": 1.0,
         "file": "slices/b000_29244_b.mp3"},
        {"index": 0, "text": "x", "start": 1.0, "end": 3.0,
         "file": "slices/b000_30244_b.mp3"}]
    se.save(tmp, segs)
    _mp3(os.path.join(tmp, "audio", "slices", "b000_29244_b.mp3"), 1.0)
    _mp3(os.path.join(tmp, "audio", "slices", "b000_30244_b.mp3"), 2.0)
    r.append(("two overlapping slices of one sentence are REJECTED (G7)",
              any(e["rule"] == "G7-overlapping-slices"
                  for e in se.validate_timeline(tmp)["errors"])))
    segs = se.load(tmp)
    segs[0]["beats"] = [{"index": 0, "text": "x", "start": 0.0, "end": 9.0}]
    se.save(tmp, segs)
    r.append(("a single clean beat does not trip G7",
              not any(e["rule"] == "G7-overlapping-slices"
                      for e in se.validate_timeline(tmp)["errors"])))

    # render-time guard is the last line of defence
    bad = {"seg_index": 0, "panel_id": "host", "dur": 3.0, "start": 0.0,
           "crop_bbox_norm": FULL, "width": 600, "height": 400,
           "beats": [{"index": 0, "text": "x", "start": 0.0, "end": 9.0}]}
    try:
        rs.seg_html(bad, os.path.join(tmp, "audio"))
        guard = False
    except RuntimeError:
        guard = True
    r.append(("the renderer itself refuses to cut narration off", guard))

    # ---- overlapping duplicate slices of ONE sentence (Overgeared seg 53:
    # three "_b" records that are suffixes of each other, so the line replayed)
    tmp3 = tempfile.mkdtemp(prefix="ovl_")
    _project(tmp3)
    _mp3(os.path.join(tmp3, "audio", "slices", "b000_2522_b.mp3"), 3.0)
    _mp3(os.path.join(tmp3, "audio", "slices", "b000_3272_b.mp3"), 2.0)
    _mp3(os.path.join(tmp3, "audio", "slices", "b000_4272_b.mp3"), 1.0)
    segs = se.load(tmp3)
    segs[0]["dur"] = 6.0; segs[0]["end"] = 6.0
    segs[0]["beats"] = [
        {"index": 0, "text": "x", "start": 0.0, "end": 1.0, "file": "slices/b000_2522_b.mp3"},
        {"index": 0, "text": "x", "start": 1.0, "end": 2.0, "file": "slices/b000_3272_b.mp3"},
        {"index": 0, "text": "x", "start": 2.0, "end": 3.0, "file": "slices/b000_4272_b.mp3"}]
    se.save(tmp3, segs)
    r.append(("three overlapping slices are rejected before repair",
              not se.validate_timeline(tmp3)["ok"]))
    got = se.repair_overlapping_slices(tmp3)
    kept = se.load(tmp3)[0]["beats"]
    r.append(("the repair leaves exactly one record for the sentence", len(kept) == 1))
    r.append(("...and keeps the LONGEST file, which contains the others",
              kept[0]["file"] == "slices/b000_2522_b.mp3"))
    r.append(("...sized to the real audio", abs((kept[0]["end"] - kept[0]["start"]) - 3.0) < 0.2))
    r.append(("...and the timeline validates afterwards", se.validate_timeline(tmp3)["ok"]))
    r.append(("a healthy segment is left alone",
              se.repair_overlapping_slices(tmp3) == []))

    # ------------------------------------------------- P1 carve binding + repair
    tmp2 = tempfile.mkdtemp(prefix="carve_")
    _project(tmp2)
    descs = [{"panel_id": "newp", "width": 600, "height": 400},
             {"panel_id": "host", "width": 600, "height": 400}]
    # "newp" reads BEFORE "host" in the unit -> the old buggy `not after` branch
    scenes = [{"scene_id": 1, "panel_ids": ["newp", "host"]}]
    se.include_panel(tmp2, "newp", scenes, descs)
    out = se.load(tmp2)
    first, second = out[0], out[1]
    ok_order = first["panel_id"] == "newp" and second["panel_id"] == "host"
    r.append(("carve puts the promoted panel first in reading order", ok_order))
    fb = first["beats"][0] if first["beats"] else None
    sbt = second["beats"][0] if second["beats"] else None
    r.append(("the EARLY window gets the _a slice, not _b",
              bool(fb) and str(fb.get("file", "")).endswith("_a.mp3")))
    r.append(("the LATE window gets the _b slice",
              bool(sbt) and str(sbt.get("file", "")).endswith("_b.mp3")))
    r.append(("every beat now lies inside its own segment (the actual bug)",
              se.validate_timeline(tmp2)["ok"]))

    # repair: hand-break a healthy pair, prove it is put back
    segs = se.load(tmp2)
    segs[0]["beats"], segs[1]["beats"] = segs[1]["beats"], segs[0]["beats"]
    se.save(tmp2, segs)
    r.append(("a swapped pair is detected as invalid",
              not se.validate_timeline(tmp2)["ok"]))
    rep = se.repair_slice_binding(tmp2)
    r.append(("repair_slice_binding re-binds the swapped pair",
              rep["n"] == 1 and se.validate_timeline(tmp2)["ok"]))
    again = se.repair_slice_binding(tmp2)
    r.append(("repair leaves an already-healthy timeline untouched", again["n"] == 0))

    # --------------------------------------------------------------- dimensions
    _png(os.path.join(tmp, "crops", "stale.png"), 900, 811)
    r.append(("board geometry comes from the FILE, not the recorded numbers",
              sb._real_dims(tmp, "stale", 900, 2582) == (900, 811)))
    r.append(("recorded numbers are used only when the file is unreadable",
              sb._real_dims(tmp, "no_such_panel", 640, 480) == (640, 480)))

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
