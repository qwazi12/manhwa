"""The sped-up export must play on an iPhone (owner, 2026-10-05: 1.25x gave
37.5 fps tagged H.264 level 6.2, which iOS refuses). Real ffmpeg on a 2 s clip."""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import speed_up
    d = tempfile.mkdtemp(prefix="spd_")
    src, dst = os.path.join(d, "in.mp4"), os.path.join(d, "out.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30",
                    "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000", "-t", "2",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", src], check=True)
    devnull = open(os.devnull, "w")
    sys.stdout, real = devnull, sys.stdout
    try:
        subprocess_run = subprocess.run
        speed_up.subprocess.run = lambda cmd, check: subprocess_run(cmd, check=check, capture_output=True)
        speed_up.speed_up(src, dst, 1.25)
    finally:
        speed_up.subprocess.run = subprocess_run
        sys.stdout = real
    p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=level,r_frame_rate,pix_fmt", "-of", "json", dst],
                                  capture_output=True, text=True).stdout)["streams"][0]
    check("H.264 level is one phones play (<= 4.2)", int(p["level"]) <= 42)
    check("30 frames per second", p["r_frame_rate"] == "30/1")
    check("yuv420p pixels", p["pix_fmt"] == "yuv420p")
    data = open(dst, "rb").read(65536)
    atoms, i = [], 0
    while i + 8 <= len(data):
        n, t = struct.unpack(">I4s", data[i:i + 8])
        atoms.append(t)
        if n < 8:
            break
        i += n
    check("index (moov) before the video data, so playback starts at once",
          b"moov" in atoms and (b"mdat" not in atoms or atoms.index(b"moov") < atoms.index(b"mdat")))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", dst],
                               capture_output=True, text=True).stdout)
    check("still 1.25x faster", abs(dur - 2 / 1.25) < 0.15)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
