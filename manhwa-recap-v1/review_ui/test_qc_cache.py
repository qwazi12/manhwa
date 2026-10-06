"""Watch the video opens fast (owner, 2026-10-05): the review checks are cached
until the timeline, the segment review, the project or the video changes."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import server
    d = tempfile.mkdtemp(prefix="qc_")
    os.makedirs(os.path.join(d, "exports"))
    for f, v in (("segments.json", []), ("project.json", {"id": "x"})):
        json.dump(v, open(os.path.join(d, f), "w"))
    open(os.path.join(d, "exports", "v.mp4"), "wb").write(b"0")
    n = {"c": 0}
    real = server._qc_bundle_build

    def counted(p, name):
        n["c"] += 1
        return {"k": n["c"]}
    server._qc_bundle_build = counted
    try:
        a = server._qc_bundle(d, "v.mp4"); b = server._qc_bundle(d, "v.mp4")
        check("second open reuses the checks", n["c"] == 1 and a == b)
        time.sleep(0.01)
        json.dump([{"seg_index": 0}], open(os.path.join(d, "segments.json"), "w"))
        os.utime(os.path.join(d, "segments.json"), None)
        server._qc_bundle(d, "v.mp4")
        check("a board edit re-checks", n["c"] == 2)
        open(os.path.join(d, "exports", "v.mp4"), "wb").write(b"00")
        server._qc_bundle(d, "v.mp4")
        check("a new video re-checks", n["c"] == 3)
        server._qc_bundle(d, "other.mp4")
        check("another video has its own checks", n["c"] == 4)
    finally:
        server._qc_bundle_build = real


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
