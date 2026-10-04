"""Pacing warnings (LongForm lesson, step 7): a long hold on one shot and a
panel shown again later are flagged as WARNINGS; nothing is blocked."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def seg(i, pid, dur, crop=None, inc=True):
    return {"seg_index": i, "panel_id": pid, "dur": dur, "crop_bbox_norm": crop, "user_included": inc}


def main():
    import storyboard_edit as se
    w = se.pacing_warnings([seg(0, "p1", 5), seg(1, "p2", 4), seg(2, "p3", 6)])
    check("normal pacing -> no warnings", w == [])
    w = se.pacing_warnings([seg(0, "p1", 7), seg(1, "p1", 7), seg(2, "p2", 3)])
    check("one shot held 14s -> long-hold warning", [x["rule"] for x in w] == ["P1-long-hold"] and w[0]["seg"] == 0)
    w = se.pacing_warnings([seg(0, "p1", 7, [0, 0, .5, .5]), seg(1, "p1", 7, [.5, .5, 1, 1])])
    check("same panel, two different crops, 14s -> fine (the crops move)", w == [])
    w = se.pacing_warnings([seg(0, "p1", 8, [0, 0, .5, .5]), seg(1, "p1", 8, [.5, .5, 1, 1]),
                            seg(2, "p1", 8, [0, .5, .5, 1])])
    check("same panel 24s across crops -> warned (over 20s)", any("across 3 crops" in x["msg"] for x in w))
    w = se.pacing_warnings([seg(0, "p1", 4), seg(1, "p2", 4), seg(2, "p1", 4)])
    check("a panel shown again later -> repeat warning on the later one",
          [(x["rule"], x["seg"]) for x in w] == [("P2-repeat-panel", 2)])
    w = se.pacing_warnings([seg(0, "p1", 4), seg(1, "p2", 4), seg(2, "p1", 4, inc=False)])
    check("segments not in the video don't count", w == [])
    w = se.pacing_warnings([seg(0, "p1", 4), seg(1, "p1", 4)])
    check("consecutive segments on one panel are not a 'repeat'", w == [])
    src = open(os.path.join(HERE, "storyboard_edit.py"), encoding="utf-8").read()
    check("pacing goes into warnings, never errors (nothing blocked)",
          "warnings.extend(pacing_warnings(segs))" in src and "errors.extend(pacing" not in src)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
