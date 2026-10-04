"""Export loudness (LongForm lesson, step 7): exports are normalised to
-14 LUFS with video stream-copied; any failure keeps the original audio.
Real ffmpeg on generated media; no network."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def make(path, vol_db):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=6",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration=6",
                    "-af", f"volume={vol_db}dB", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", path], check=True, capture_output=True)


def vcodec_hash(path):
    r = subprocess.run(["ffmpeg", "-i", path, "-map", "0:v", "-c", "copy", "-f", "md5", "-"],
                       capture_output=True, text=True)
    return r.stdout.strip()


def main():
    import audio_level as al
    d = tempfile.mkdtemp(prefix="lufs_")
    quiet = os.path.join(d, "quiet.mp4")
    make(quiet, -30)
    before = float(al.measure(quiet)["input_i"])
    vh = vcodec_hash(quiet)
    rep = al.normalise(quiet, -14.0)
    after = float(al.measure(quiet)["input_i"])
    check("a quiet export is raised to about -14 LUFS", rep["applied"] and abs(after + 14) <= 1.0)
    check("...from where it was", before < -25 and rep["before"] == round(before, 1))
    check("the video stream is untouched (stream copy)", vcodec_hash(quiet) == vh)
    check("already at target -> left alone", al.normalise(quiet, -14.0)["applied"] is False)

    os.environ["EXPORT_LUFS"] = "off"
    check("EXPORT_LUFS=off skips it", al.normalise(quiet)["why"] == "EXPORT_LUFS=off")
    os.environ["EXPORT_LUFS"] = "-16"
    check("EXPORT_LUFS sets the target", al.target() == -16.0)
    os.environ.pop("EXPORT_LUFS")
    check("default target is -14", al.target() == -14.0)

    bad = os.path.join(d, "bad.mp4")
    open(bad, "wb").write(b"not a video")
    r = al.normalise(bad, -14.0)
    check("a broken file never raises and is left as it was",
          r["applied"] is False and open(bad, "rb").read() == b"not a video")
    src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    check("the export runs it before the speed-up", src.index("_al.normalise(out)") < src.index("speed_up.py"))


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
