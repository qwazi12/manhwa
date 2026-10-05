"""The board as data for the new Chapter page (rebuild step 5): same rules as
the classic board — in the video = ticked and not rejected, times are video
positions, every panel says where it went. Temp project; no network."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import board_data
    p = tempfile.mkdtemp(prefix="bd_")
    descs = [{"panel_id": f"page001_panel_00{i}", "width": 900, "height": 600, "ocr_text": "", "visual_description": f"scene {i}"}
             for i in range(1, 6)]
    descs[3]["role"] = "bubble"
    json.dump(descs, open(os.path.join(p, "descriptions.json"), "w"))
    segs = [
        {"seg_index": 0, "panel_id": "page001_panel_001", "start": 0, "dur": 5, "user_included": True,
         "beats": [{"index": 0, "start": 0, "end": 4, "text": "He walks in."}]},
        {"seg_index": 1, "panel_id": "page001_panel_002", "start": 5, "dur": 13, "user_included": False,
         "beats": [{"index": 1, "start": 5, "end": 9, "text": "Not shown."}]},
        {"seg_index": 2, "panel_id": "page001_panel_003", "start": 18, "dur": 13, "user_included": True,
         "beats": [{"index": 2, "start": 18, "end": 22, "text": "Lightning."}], "crop_bbox_norm": [0, 0, 0.8, 0.6]},
        {"seg_index": 3, "panel_id": "page001_panel_003", "start": 31, "dur": 4, "user_included": True, "beats": []},
    ]
    json.dump(segs, open(os.path.join(p, "segments.json"), "w"))
    json.dump([{"scene_id": 4, "text": "Folded text", "panel_ids": ["page001_panel_005"]}], open(os.path.join(p, "script.json"), "w"))
    b = board_data.build(p, {"2": {"status": "pending"}}, junk_reason=lambda d: "speech bubble" if d.get("role") == "bubble" else "")
    by = {s["seg_index"]: s for s in b["segments"]}
    check("unticked segment is not in the video", by[1]["in_video"] is False)
    check("video times skip what's left out (seg 2 starts at 0:05)", by[2]["video_start"] == 5 and by[0]["video_start"] == 0)
    check("long hold flagged", "long hold" in by[2]["flags"])
    check("same picture for 17 s across 2 segments flagged", any(f.startswith("same image") for f in by[2]["flags"]))
    check("a planned crop reports how much it keeps", by[2]["crop"]["status"] == "sub" and by[2]["crop"]["keeps"] == 48)
    check("a segment with no lines is a silent hold", by[3]["silent"] is True)
    pl = {x["panel_id"]: x for x in b["panels"]}
    check("panels say where they went", pl["page001_panel_001"]["place"] == "on_screen"
          and pl["page001_panel_004"]["place"] == "left_out" and pl["page001_panel_004"]["left_out_why"] == "speech bubble"
          and pl["page001_panel_005"]["place"] == "folded" and pl["page001_panel_005"]["unit"] == 4)
    check("summary counts", b["summary"]["in_video"] == 3 and b["summary"]["runtime"] == 22.0 and b["summary"]["left_out"] == 1)
    b2 = board_data.build(p, {"0": {"status": "rejected"}})
    check("a rejected segment is not in the video", not next(s for s in b2["segments"] if s["seg_index"] == 0)["in_video"])

    import publish_queue as pq
    root = tempfile.mkdtemp(prefix="bdq_")
    pq.add(root, [{"project": "a", "name": "v.mp4"}])
    iid = pq.load(root)["items"][0]["id"]
    pq.mark(root, iid, status="failed", error="x")
    pq.remove(root, iid)
    check("a failed queue row can be removed", pq.load(root)["items"][0]["status"] == "removed")


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
