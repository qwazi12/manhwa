#!/usr/bin/env python3
"""
Final-render speed pass — DORMANT by design (never auto-invoked by the pipeline).

Applies a uniform playback speed-up to an already-finished MP4: video via
setpts=PTS/FACTOR, audio via chained atempo. This is the agreed way to get the
"feels better at 1.5x" pacing WITHOUT regenerating narration faster at the TTS
level (which can degrade synthesis). Run it last, on the picture-locked render.

Usage:
    python speed_up.py input.mp4 [output.mp4] [factor]
    python speed_up.py my-video/recap_hyperframes.mp4            # -> ..._1.5x.mp4
    python speed_up.py in.mp4 out.mp4 1.5
"""
import os
import subprocess
import sys


def atempo_chain(factor):
    """ffmpeg's atempo accepts 0.5..2.0 per filter; chain for larger factors."""
    parts, remaining = [], factor
    while remaining > 2.0:
        parts.append("atempo=2.0"); remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5"); remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def speed_up(src, dst, factor):
    v = f"setpts=PTS/{factor}"
    a = atempo_chain(factor)
    cmd = ["ffmpeg", "-y", "-i", src,
           "-filter:v", v, "-filter:a", a,
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-c:a", "aac", "-b:a", "192k", dst]
    subprocess.run(cmd, check=True)
    return dst


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    factor = float(sys.argv[3]) if len(sys.argv) > 3 else 1.5
    if len(sys.argv) > 2:
        dst = sys.argv[2]
    else:
        base, ext = os.path.splitext(src)
        dst = f"{base}_{factor:g}x{ext}"
    out = speed_up(src, dst, factor)
    print(f"Wrote {out} ({factor:g}x)")


if __name__ == "__main__":
    main()
