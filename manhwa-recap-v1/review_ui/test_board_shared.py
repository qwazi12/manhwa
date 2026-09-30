"""Board: every image shows its OWN sentence; "shared" only when one sentence
really plays on 2+ images (2026-09-30).

The old rule labelled every on-screen image after the first in a narration
unit "↳ shared narration ¶N (image k of n)" and hid its sentence. On ch.44 v2
that was ~40 rows, while the timeline had exactly ONE sentence on two images.

Run: python3 test_board_shared.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..")]
import storyboard
import matcher

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    pdir = tempfile.mkdtemp(prefix="board_")
    descs = [{"panel_id": f"page001_panel_00{i}", "width": 800, "height": 600,
              "visual_description": f"Swinging a sword, a warrior in scene {i}",
              "ocr_text": "", "ok": True} for i in range(1, 5)]
    B = lambda i, t: {"index": i, "text": t, "start": 0, "end": 1}
    segs = [
        {"seg_index": 0, "panel_id": "page001_panel_001", "start": 0, "end": 2, "dur": 2,
         "beats": [B(0, "The guard drew his blade in the rain.")], "clip": "c0"},
        {"seg_index": 1, "panel_id": "page001_panel_002", "start": 2, "end": 4, "dur": 2,
         "beats": [B(1, "The first cut missed by an inch and bit the wall.")], "clip": "c1"},
        # one sentence deliberately spanning two images
        {"seg_index": 2, "panel_id": "page001_panel_003", "start": 4, "end": 6, "dur": 2,
         "beats": [B(2, "Steel met steel twice, then a third time, and neither gave ground.")], "clip": "c2"},
        {"seg_index": 3, "panel_id": "page001_panel_004", "start": 6, "end": 8, "dur": 2,
         "beats": [B(2, "Steel met steel twice, then a third time, and neither gave ground.")], "clip": "c3"},
    ]
    scenes = [{"scene_id": 0, "panel_ids": [d["panel_id"] for d in descs], "text": "…"}]
    for name, obj in (("descriptions.json", descs), ("segments.json", segs),
                      ("script.json", scenes), ("project.json", {"id": "t"})):
        json.dump(obj, open(os.path.join(pdir, name), "w"))
    out = storyboard.build_storyboard_html(pdir, matcher, {}, {}, False)

    check("no row carries the old misleading 'shared narration' label",
          "shared narration" not in out)
    check("the 2nd image in a unit shows ITS OWN sentence",
          "The first cut missed by an inch" in out)
    check("only the two images that really share a sentence get the badge",
          out.count("↔ shared sentence") == 2)
    check("the badge says which images share it",
          "also on page001_panel_004" in out and "also on page001_panel_003" in out)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print(f"\n{n}/{len(R)} passed")
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
