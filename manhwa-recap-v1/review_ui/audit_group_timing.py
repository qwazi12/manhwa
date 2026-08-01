"""Adversarial audit of the narration-group timing model (commit 67cb4c2).

No ffmpeg needed: the group path of set_duration never touches audio.
"""
import json, os, shutil, sys, tempfile
sys.path.insert(0, "/home/user/manhwa/manhwa-recap-v1/review_ui")
import storyboard_edit as se

R = []
def check(name, ok, note=""):
    R.append((name, ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" :: " + str(note)) if note else ""))

def mk(segs, scenes):
    p = tempfile.mkdtemp(prefix="audit_")
    os.makedirs(os.path.join(p, "clips"))
    json.dump(segs, open(os.path.join(p, "segments.json"), "w"))
    json.dump(scenes, open(os.path.join(p, "script.json"), "w"))
    return p

def seg(si, pid, sc, start, dur, beats):
    return {"seg_index": si, "panel_id": pid, "scene_id": sc, "start": start,
            "dur": dur, "end": round(start + dur, 3), "group_dur": None,
            "clip": f"clips/seg_{si:03d}.mp4", "beats": beats}

def beat(i, s, e, txt="x"):
    return {"index": i, "text": txt, "start": s, "end": e}

def occupied(s):
    return round(max(b["end"] for b in s["beats"]) - s["start"], 3) if s["beats"] else 0.0


print("\n=== 1. AUDIO-COVERAGE FLOOR IN THE GROUP PATH ===")
# Two images share narration unit 1 (12.0s). Each carries a real sentence.
segs = [
    seg(0, "p1", 1, 0.0, 6.0, [beat(0, 0.0, 5.5)]),    # 5.5s of speech
    seg(1, "p2", 1, 6.0, 6.0, [beat(1, 6.0, 11.5)]),   # 5.5s of speech
    seg(2, "p3", 2, 12.0, 5.0, [beat(2, 12.0, 16.0)]),
]
scenes = [{"scene_id": 1, "panel_ids": ["p1", "p2"]},
          {"scene_id": 2, "panel_ids": ["p3"]}]
p = mk(segs, scenes)
out = se.set_duration(p, 1, 9.0)          # push image 2 to 9s -> sibling gets 3s
s0 = out[0]
check("sibling shrunk below its own narration (regression)",
      s0["dur"] < occupied(s0),
      f"dur={s0['dur']}s vs narration occupying {occupied(s0)}s "
      f"-> {round(occupied(s0)-s0['dur'],3)}s of speech is cut at render")
check("old single-segment path would have REFUSED this",
      se.MIN_SEG_DUR < occupied(s0),
      "the pre-existing guard was max(MIN_SEG, _occupied); the group path checks MIN_SEG_DUR only")
# and the grown sibling gains dead air
s1 = out[1]
check("grown sibling gains unflagged silence",
      s1["dur"] > occupied(s1),
      f"dur={s1['dur']}s vs audio {occupied(s1)}s -> {round(s1['dur']-occupied(s1),3)}s dead air")
shutil.rmtree(p)


print("\n=== 2. MANUAL LOCK IS ONE-WAY (group deadlocks) ===")
segs = [
    seg(0, "p1", 1, 0.0, 4.0, [beat(0, 0.0, 3.0)]),
    seg(1, "p2", 1, 4.0, 4.0, [beat(1, 4.0, 7.0)]),
    seg(2, "p3", 1, 8.0, 4.0, [beat(2, 8.0, 11.0)]),
]
scenes = [{"scene_id": 1, "panel_ids": ["p1", "p2", "p3"]}]
p = mk(segs, scenes)
se.set_duration(p, 0, 3.0)
se.set_duration(p, 1, 3.0)
modes = [s.get("duration_mode") for s in se.load(p)]
try:
    se.set_duration(p, 2, 3.0)
    check("third edit deadlocks the group", False, "expected ValueError, got none")
except ValueError as e:
    check("third edit deadlocks the group", "set manually" in str(e), str(e))
check("no way to clear duration_mode back to auto",
      not any("duration_mode" in l and "auto" in l
              for l in open("/home/user/manhwa/manhwa-recap-v1/review_ui/server.py")),
      f"modes after 2 edits = {modes}; no unlock endpoint in server.py")
shutil.rmtree(p)


print("\n=== 3. group_dur GOES STALE (other ops never update it) ===")
segs = [
    seg(0, "p1", 1, 0.0, 6.0, [beat(0, 0.0, 3.0)]),
    seg(1, "p2", 1, 6.0, 6.0, [beat(1, 6.0, 8.0), beat(2, 8.5, 10.5)]),
    seg(2, "p3", 1, 12.0, 6.0, [beat(3, 12.0, 15.0)]),
    seg(3, "p4", 2, 18.0, 5.0, [beat(4, 18.0, 22.0)]),
]
scenes = [{"scene_id": 1, "panel_ids": ["p1", "p2", "p3"]},
          {"scene_id": 2, "panel_ids": ["p4"]}]
p = mk(segs, scenes)
se.set_duration(p, 0, 4.0)                      # stamps group_dur = 18.0 on the group
before = se.load(p)
total_before = round(before[-1]["start"] + before[-1]["dur"], 3)
se.delete_line(p, 1, 2)                         # seg 1 drops its 2nd sentence (2.0s)
mid = se.load(p)
total_mid = round(mid[-1]["start"] + mid[-1]["dur"], 3)
check("delete_line shrinks a group member without touching group_dur",
      mid[1]["group_dur"] == 18.0 and total_mid < total_before,
      f"group_dur still {mid[1]['group_dur']}, real group total now "
      f"{round(sum(s['dur'] for s in mid[:3]),3)}s; project {total_before}->{total_mid}s")
se.set_duration(p, 1, 6.0)                      # next group edit trusts the stale stamp
after = se.load(p)
total_after = round(after[-1]["start"] + after[-1]["dur"], 3)
check("next group edit silently re-inflates the timeline to the stale total",
      total_after > total_mid,
      f"project runtime {total_mid}s -> {total_after}s with no user request to lengthen it")
shutil.rmtree(p)


print("\n=== 4. TWO DIFFERENT MINIMUMS ===")
check("MIN_SEG (old paths) != MIN_SEG_DUR (group path)",
      se.MIN_SEG != se.MIN_SEG_DUR,
      f"move_boundary/delete_line floor={se.MIN_SEG}s, rebalance floor={se.MIN_SEG_DUR}s "
      f"-> a {se.MIN_SEG}s segment is reachable but then un-editable by duration")


print("\n=== 5. VALIDATION GATE ===")
src = open("/home/user/manhwa/manhwa-recap-v1/review_ui/storyboard_edit.py").read()
srv = open("/home/user/manhwa/manhwa-recap-v1/review_ui/server.py").read()
check("validate_timeline() absent", "validate_timeline" not in src and "validate_timeline" not in srv)
check("rebalance_group has exactly one production caller",
      src.count("rebalance_group(") == 2,   # def + the single call in set_duration
      f"occurrences incl. def = {src.count('rebalance_group(')}")
check("include_panel does not rebalance", "rebalance_group" not in
      src[src.index("def include_panel"):src.index("def exclude_panel")])
check("exclude_panel does not rebalance", "rebalance_group" not in
      src[src.index("def exclude_panel"):src.index("def reorder")])


print("\n=== 6. UI GROUP MODEL vs BACKEND GROUP MODEL ===")
sb = open("/home/user/manhwa/manhwa-recap-v1/review_ui/storyboard.py").read()
check("board never reads group_dur / duration_mode",
      "group_dur" not in sb and "duration_mode" not in sb,
      "no live 12.0/12.0s chip, no lock indicator, no distribute-evenly control")
check("sibling rows print no narration text of their own",
      "shared narration" in sb and sb.count("btxt") == 3,
      "text of beats living on sibling segments is not rendered anywhere")

fails = [r for r in R if not r[1]]
print(f"\n{len(R)-len(fails)}/{len(R)} audit assertions confirmed")
