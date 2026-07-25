"""M1-M3 unit tests: free seg placement (assign to any row)."""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.expanduser("~/dev/manhwa/manhwa-recap-v1/review_ui"))
import storyboard_edit as se

def fixture():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "crops"), exist_ok=True)
    os.makedirs(os.path.join(d, "clips"), exist_ok=True)
    segs = []
    t = 0.0
    for i, (pid, dur) in enumerate([("pA", 5.0), ("pB", 4.0), ("pC", 6.0)]):
        segs.append({"seg_index": i, "panel_id": pid,
                     "panel_file": os.path.join(d, "crops", pid + ".png"),
                     "start": t, "dur": dur, "end": t + dur,
                     "clip": f"clips/seg_{i:03d}.mp4",
                     "crop": {"box": [0, 0, 10, 10]},
                     "beats": [{"index": i, "text": f"line {i}", "start": t, "end": t + dur}]})
        open(os.path.join(d, "clips", f"seg_{i:03d}.mp4"), "w").write("x")
        t += dur
    json.dump(segs, open(os.path.join(d, "segments.json"), "w"))
    descs = [{"panel_id": p, "ok": True} for p in ("pA", "pB", "pC", "pD")]
    return d, descs

fails = []
def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + ("" if cond else " :: " + detail))
    if not cond:
        fails.append(name)

# 1. plain assign: artwork changes, timing/order/narration untouched
d, descs = fixture()
before = json.load(open(os.path.join(d, "segments.json")))
segs = se.assign_panel(d, 0, "pD", descs)
s0 = segs[0]
check("assign changes panel_id", s0["panel_id"] == "pD")
check("panel_file follows", s0["panel_file"].endswith("pD.png"))
check("narration untouched", s0["beats"] == before[0]["beats"])
check("duration untouched", s0["dur"] == before[0]["dur"])
check("playback order untouched", [s["seg_index"] for s in segs] == [0, 1, 2])
check("total runtime conserved",
      round(segs[-1]["start"] + segs[-1]["dur"], 3)
      == round(before[-1]["start"] + before[-1]["dur"], 3))
check("marked user_assigned", s0.get("user_assigned") is True)
check("stale sub-crop dropped", "crop" not in s0)
check("clip marked stale for re-render",
      not os.path.exists(os.path.join(d, "clips", "seg_000.mp4")))

# 2. dropping onto an OCCUPIED row is legal (a row may hold several segs)
segs = se.assign_panel(d, 1, "pC", descs)
check("occupied row accepts a second seg",
      sorted(s["panel_id"] for s in segs) == ["pC", "pC", "pD"],
      str([s["panel_id"] for s in segs]))
check("displaced nothing", len(segs) == 3)

# 3. shift-drop: also re-sequence to that panel's reading-order position
d2, descs2 = fixture()
segs = se.assign_panel(d2, 0, "pC", descs2, move_here=True)
order = [s["seg_index"] for s in segs]
check("move_here re-sequences", order[-1] == 0, str(order))
check("timeline still contiguous after move",
      all(round(segs[i]["end"], 3) == round(segs[i + 1]["start"], 3)
          for i in range(len(segs) - 1)))
check("no narration lost", sum(len(s["beats"]) for s in segs) == 3)

# 4. guardrails
for bad, args in (("unknown panel", (d2, 0, "nope", descs2)),
                  ("unknown segment", (d2, 99, "pA", descs2))):
    try:
        se.assign_panel(*args)
        check("rejects " + bad, False, "no error raised")
    except ValueError:
        check("rejects " + bad, True)

shutil.rmtree(d); shutil.rmtree(d2)
print(("\nALL PASS" if not fails else "\nFAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
