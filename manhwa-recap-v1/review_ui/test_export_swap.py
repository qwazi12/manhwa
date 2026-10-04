"""Export side-folder swap (LongForm lesson, step 7): the video is built in
exports/.partial and only the finished file is moved into exports/, so a
failed or stopped export never shows a half-written video. Real ffmpeg on
tiny generated clips; no network."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def clip(path, secs):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=10:duration={secs}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:duration={secs}", "-af", "volume=-28dB",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", path],
                   check=True, capture_output=True)


def main():
    import server
    pdir = tempfile.mkdtemp(prefix="exp_")
    os.makedirs(os.path.join(pdir, "clips"))
    json.dump({"series": "", "chapter": ""}, open(os.path.join(pdir, "project.json"), "w"))
    segs = []
    for i in range(2):
        clip(os.path.join(pdir, "clips", f"seg_{i:03d}.mp4"), 2)
        segs.append({"seg_index": i, "start": i * 2.0, "end": i * 2.0 + 2, "dur": 2.0, "panel_id": f"p{i}",
                     "user_included": True, "clip": f"clips/seg_{i:03d}.mp4", "beats": []})
    saved = (server.active_project_dir, server.load_segments, server._gate_timeline)
    server.active_project_dir = lambda: pdir
    server.load_segments = lambda: segs
    server._gate_timeline = lambda *a, **k: None
    exp = os.path.join(pdir, "exports")
    try:
        res = server._do_export(1.25)
        mp4s = [f for f in os.listdir(exp) if f.endswith(".mp4")]
        check("the finished export lands in exports/", mp4s == [res["output"]] and res["output"].endswith("_1.25x.mp4"))
        check("...and nothing is left in the side folder", os.listdir(os.path.join(exp, ".partial")) == [])
        check("the export was levelled for loudness", res["loudness"].get("applied") is True)
        check("the exports list never shows the side folder",
              all(not e["name"].startswith(".") for e in server.list_exports()["exports"]))

        # a failure mid-way (the speed-up) leaves NO new file in exports/
        real_run = subprocess.run

        def failing(cmd, *a, **k):
            if any("speed_up.py" in str(c) for c in cmd):
                raise subprocess.CalledProcessError(1, cmd)
            return real_run(cmd, *a, **k)
        server.subprocess.run = failing
        try:
            server._do_export(1.25)
            raised = False
        except subprocess.CalledProcessError:
            raised = True
        finally:
            server.subprocess.run = real_run
        after = [f for f in os.listdir(exp) if f.endswith(".mp4")]
        check("a failed export raises and adds nothing half-written to exports/", raised and after == mp4s)
        server._do_export(1.0)
        check("the next export clears the failed one's leftovers",
              os.listdir(os.path.join(exp, ".partial")) == [])
    finally:
        server.active_project_dir, server.load_segments, server._gate_timeline = saved


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
