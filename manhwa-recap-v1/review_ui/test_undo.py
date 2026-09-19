"""Undo for the timing & motion track.

Fifteen routes mutate segments.json — the render manifest — and several are not
cleanly invertible: carving a beat SLICES its mp3, including a panel
SYNTHESISES audio. Implementing fifteen inverse operations would be fifteen
chances to drift a manifest that decides what actually renders.

So undo is a snapshot stack: save() copies the pre-edit manifest aside, _log()
names it with the op that followed, and undo() restores it byte-for-byte.

What these pin:
1. A snapshot is taken BEFORE the write, and holds the OLD state.
2. Undo restores exactly, and pops — so repeated undo walks backwards rather
   than flip-flopping between two states.
3. Only the segments that actually changed have their clips invalidated.
4. The stack is bounded, so editing all day cannot fill the volume.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def seg(i, dur=3.0, start=0.0, text="a line of narration"):
    return {"seg_index": i, "panel_id": "page001_panel_%03d" % i,
            "panel_file": "/tmp/p%d.png" % i, "width": 800, "height": 1200,
            "crop_bbox_norm": [0.0, 0.0, 1.0, 1.0],
            "focus_source": "subshot", "focus_confidence": 1.0,
            "start": start, "end": start + dur, "dur": dur,
            "user_included": True,
            "beats": [{"index": i, "start": start, "end": start + dur - 0.3,
                       "text": text}]}


def build(pdir, n=4):
    segs, t = [], 0.0
    for i in range(n):
        segs.append(seg(i, 3.0, t))
        t += 3.0
    with open(os.path.join(pdir, "segments.json"), "w") as f:
        json.dump(segs, f)
    os.makedirs(os.path.join(pdir, "clips"), exist_ok=True)
    for i in range(n):
        open(os.path.join(pdir, "clips", "seg_%03d.mp4" % i), "w").write("x")
    return segs


def main():
    import storyboard_edit as E

    pdir = tempfile.mkdtemp(prefix="undo_")
    build(pdir)

    # ============ a snapshot holds the state BEFORE the write
    segs = E.load(pdir)
    segs[1]["dur"] = 9.0
    E.save(pdir, segs)
    E._log(pdir, "set_duration", seg=1)

    st = E.undo_stack(pdir)
    check("an edit leaves exactly one undo point", len(st) == 1)
    check("...named after the op that caused it", st[0]["op"] == "set_duration")
    snap = json.load(open(os.path.join(E._undo_dir(pdir), st[0]["id"] + ".json")))
    check("...holding the state from BEFORE the edit, not after",
          snap[1]["dur"] == 3.0)
    check("...while the live manifest holds the edit",
          E.load(pdir)[1]["dur"] == 9.0)

    # ============ undo restores, and pops
    res = E.undo(pdir)
    check("undo reports which op it walked back", res["undid"] == "set_duration")
    check("...and the manifest is restored", E.load(pdir)[1]["dur"] == 3.0)
    check("...and the undo point is consumed", len(E.undo_stack(pdir)) == 0)

    try:
        E.undo(pdir)
        check("undo with nothing to undo is refused", False)
    except ValueError as e:
        check("undo with nothing to undo is refused", True)
        check("...with a message an operator can read",
              "nothing to undo" in str(e))

    # ============ repeated undo WALKS BACK rather than flip-flopping.
    # This is why undo does not go through save(): save() snapshots, so undoing
    # through it would push the undone state straight back on the stack and the
    # second undo would redo the first.
    build(pdir)
    for d in (4.0, 5.0, 6.0):
        s2 = E.load(pdir)
        s2[0]["dur"] = d
        E.save(pdir, s2)
        E._log(pdir, "set_duration", seg=0)
    check("three edits leave three undo points", len(E.undo_stack(pdir)) == 3)
    check("the live value is the newest edit", E.load(pdir)[0]["dur"] == 6.0)
    E.undo(pdir)
    check("one undo steps back one edit", E.load(pdir)[0]["dur"] == 5.0)
    E.undo(pdir)
    check("...two steps back, not forward again", E.load(pdir)[0]["dur"] == 4.0)
    E.undo(pdir)
    check("...and back to the original", E.load(pdir)[0]["dur"] == 3.0)
    check("the stack is now empty", len(E.undo_stack(pdir)) == 0)

    # ============ only genuinely changed segments lose their clips
    build(pdir)
    s3 = E.load(pdir)
    s3[2]["dur"] = 7.5
    E.save(pdir, s3)
    E._log(pdir, "set_duration", seg=2)
    for i in range(4):                      # pretend everything re-rendered
        open(os.path.join(pdir, "clips", "seg_%03d.mp4" % i), "w").write("x")
    E.undo(pdir)
    gone = [i for i in range(4)
            if not os.path.exists(os.path.join(pdir, "clips", "seg_%03d.mp4" % i))]
    check("undo invalidates the clip of the segment it changed", 2 in gone)
    check("...and leaves untouched segments' clips alone", gone == [2])

    # ============ the stack is bounded
    build(pdir)
    for k in range(E.UNDO_DEPTH + 8):
        s4 = E.load(pdir)
        s4[0]["dur"] = 3.0 + (k + 1) * 0.1
        E.save(pdir, s4)
        E._log(pdir, "set_duration", seg=0)
    check("the undo stack is capped, not unbounded",
          len(E.undo_stack(pdir)) == E.UNDO_DEPTH)
    files = os.listdir(E._undo_dir(pdir))
    check("...and the pruned snapshots are removed from disk, not orphaned",
          len([f for f in files if f.endswith(".json")]) == E.UNDO_DEPTH)
    check("...leaving no stray .op labels behind",
          len([f for f in files if f.endswith(".op")]) == E.UNDO_DEPTH)

    # ============ undo is itself logged, but is not an undoable edit
    log = [json.loads(l) for l in
           open(os.path.join(pdir, "edits.log.jsonl"), encoding="utf-8")]
    check("undo is recorded in the edit log", any(x["op"] == "undo" for x in log))
    n_before = len(E.undo_stack(pdir))
    E.undo(pdir)
    check("...and undoing does not itself create a new undo point",
          len(E.undo_stack(pdir)) == n_before - 1)

    # ============ a restored manifest is still renderable
    segs_now = E.load(pdir)
    check("the restored timeline is contiguous",
          all(abs(segs_now[i]["start"] - segs_now[i - 1]["end"]) < 0.01
              for i in range(1, len(segs_now))))
    check("...and every segment still carries its beats",
          all(s.get("beats") for s in segs_now))

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
