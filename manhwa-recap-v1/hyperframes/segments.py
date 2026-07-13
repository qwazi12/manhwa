#!/usr/bin/env python3
"""
Segment model for the per-segment render/review layer.

A SEGMENT is the render unit (vs the beat = narration/decision unit). It's a
maximal run of consecutive beats that share the same matched panel — so several
beats that hold one panel become ONE clip instead of several identical ones.
This is the "Beat = decision, Segment = render" hierarchy: precision of
beat-level matching, efficiency of panel-level rendering.

build_segments(shots, beats) -> list of segment dicts:
    {
      "seg_index": int,
      "panel_id": str, "panel_file": str,
      "start": float, "end": float, "dur": float,
      "beats": [ {"index","start","end","text","audio_rel"} ],  # within-seg
      "held": bool,          # True if this segment continues the prior panel run
                             # (always False here — a new segment == new panel)
      "clip": "clips/seg_000.mp4",
    }

Merge rule (v1): same panel_id -> same segment. Crop/motion-based splits come
later once sub-shots exist. A segment carries all its beats' audio so narration
timing is preserved exactly when clips are concatenated.
"""


def build_segments(shots):
    segments = []
    cur = None
    for s in shots:
        if cur is None or s["panel_id"] != cur["panel_id"] or s.get("crop_bbox_norm") != cur.get("crop_bbox_norm"):
            if cur is not None:
                segments.append(cur)
            cur = {
                "seg_index": len(segments),
                "panel_id": s["panel_id"],
                "panel_file": s.get("panel_file"),
                "width": s.get("width"),
                "height": s.get("height"),
                "crop_bbox_norm": s.get("crop_bbox_norm"),
                "focus_source": s.get("focus_source"),
                "focus_reason": s.get("focus_reason"),
                "focus_confidence": s.get("focus_confidence"),
                "start": float(s["start"]),
                "end": float(s["end"]),
                "beats": [],
            }
        cur["beats"].append({
            "index": s["index"],
            "start": float(s["start"]),
            "end": float(s["end"]),
            "text": s.get("beat_text", ""),
        })
        cur["end"] = float(s["end"])
    if cur is not None:
        segments.append(cur)

    for seg in segments:
        seg["dur"] = round(seg["end"] - seg["start"], 3)
        seg["clip"] = f"clips/seg_{seg['seg_index']:03d}.mp4"
    return segments


if __name__ == "__main__":
    import json
    import os
    import sys
    HERE = os.path.dirname(os.path.abspath(__file__))
    RECAP = os.path.abspath(os.path.join(HERE, ".."))
    bs = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        RECAP, "build_test", "beatsheet_full.json")
    shots = json.load(open(bs))
    segs = build_segments(shots)
    print(f"{len(shots)} beats -> {len(segs)} segments "
          f"(avg {len(shots)/len(segs):.1f} beats/segment)")
    multi = [s for s in segs if len(s["beats"]) > 1]
    print(f"{len(multi)} multi-beat segments (a held panel rendered once)")
    for s in segs[:8]:
        print(f"  seg {s['seg_index']:3} [{s['panel_id']:20}] "
              f"{s['start']:6.1f}-{s['end']:6.1f}s ({s['dur']:4.1f}s) "
              f"{len(s['beats'])} beat(s)")
