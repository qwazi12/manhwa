"""Renderer-epoch staleness tests (Session 23).

Why this exists: the render step only ever rebuilt clips whose FILE was
missing, so shipping a renderer change (new framing/motion) silently reused
old-looking clips — the export never showed the fix. Clips are now stamped
with render_segments.RENDER_EPOCH and a mismatch counts as "needs render".

Run: python3 test_render_epoch.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "hyperframes"))
import render_segments as rs
import server as srv


def main():
    pdir = tempfile.mkdtemp(prefix="epochtest_")
    os.makedirs(os.path.join(pdir, "clips"))
    segs = [{"seg_index": i, "clip": f"clips/seg_{i:03d}.mp4"} for i in range(3)]
    for s in segs:
        open(os.path.join(pdir, s["clip"]), "wb").write(b"x")
    srv.active_project_dir = lambda: pdir

    results = [
        ("unstamped existing clips need render",
         srv.needs_render(segs, pdir) == [0, 1, 2]),
    ]
    for s in segs:
        srv._stamp_epoch(s["seg_index"])
    results.append(("stamped at current epoch -> none need render",
                    srv.needs_render(segs, pdir) == []))
    rs.RENDER_EPOCH += 1
    results.append(("renderer bump invalidates every clip",
                    srv.needs_render(segs, pdir) == [0, 1, 2]))
    rs.RENDER_EPOCH -= 1
    os.remove(os.path.join(pdir, segs[1]["clip"]))
    results.append(("missing clip still detected",
                    srv.needs_render(segs, pdir) == [1]))

    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in results if ok)
    print(f"\n{n}/{len(results)} passed")
    return 0 if n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
