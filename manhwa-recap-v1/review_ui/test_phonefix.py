"""'Fix for phones' (owner, 2026-10-05): an export made before the speed-up fix
(37.5 fps, level 6.2) is flagged, re-encoded in place under the same name and
date, and then reads as phone-playable. Real ffmpeg on a 2 s clip, temp root."""
import json
import os
import subprocess
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
    import ingest
    import server
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="pf_")
    pid = "series-p_1"
    ed = os.path.join(root, pid, "exports")
    os.makedirs(ed)
    json.dump([], open(os.path.join(root, pid, "segments.json"), "w"))
    json.dump({"id": pid, "url": "https://example.com/p/1"}, open(os.path.join(root, pid, "project.json"), "w"))
    name = "final_test_1.25x.mp4"
    bad = os.path.join(ed, name)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000", "-t", "2",
                    "-filter:v", "setpts=PTS/1.25", "-c:v", "libx264", "-level:v", "6.2",
                    "-c:a", "aac", "-shortest", bad], check=True)
    old_mtime = os.path.getmtime(bad) - 3600
    os.utime(bad, (old_mtime, old_mtime))
    saved = ingest.PROJECTS
    ingest.PROJECTS = root
    try:
        check("an old 1.25x export is flagged as not phone-playable", server._phone_ok(bad) is False)
        c = TestClient(server.app)
        r = c.post("/api/exports/phonefix", json={"project": pid, "name": name}).json()
        check("fix starts a job", r.get("job"))
        t0 = time.time()
        while time.time() - t0 < 60 and server.JOBS[r["job"]]["status"] in ("queued", "running"):
            time.sleep(0.2)
        j = server.JOBS[r["job"]]
        check("the job finishes", j["status"] == "done")
        check("same file name, now phone-playable", os.path.exists(bad) and server._phone_ok(bad) is True)
        check("its date is kept (retention and 'newest' unchanged)", abs(os.path.getmtime(bad) - old_mtime) < 2)
        check("no temporary file left, none listed as a video",
              sorted(os.listdir(ed)) == [name])
        r2 = c.post("/api/exports/phonefix", json={"project": pid, "name": name}).json()
        check("fixing a playable video does nothing", r2.get("job") is None)
        check("an unknown video is refused",
              c.post("/api/exports/phonefix", json={"project": pid, "name": "nope.mp4"}).status_code == 404)
    finally:
        ingest.PROJECTS = saved


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
