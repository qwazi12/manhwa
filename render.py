"""
Rendering. Three passes, all FFmpeg:

1. One motion clip per shot — vertical pan for tall webtoon slices,
   Ken Burns zoom (alternating in/out) for everything else.
2. Concat the clips (stream copy, identical encode settings).
3. Burn subtitles and mux the voice track.

All ffmpeg calls run with cwd=build_dir and relative paths so the
subtitles filter never needs path escaping.
"""

import os
import subprocess
from PIL import Image

import config


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({' '.join(cmd[:6])}...):\n{r.stderr[-2000:]}")


def _has_filter(name: str) -> bool:
    """Check if ffmpeg has a particular filter compiled in."""
    r = subprocess.run(
        ["ffmpeg", "-filters"],
        capture_output=True, text=True,
    )
    return f" {name} " in r.stdout or f" {name}\n" in r.stdout


def _srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def write_srt(beats, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, b in enumerate(beats, 1):
            f.write(f"{i}\n{_srt_time(b['start'])} --> "
                    f"{_srt_time(b['end'])}\n{b['text']}\n\n")


def _motion_mode(image_path: str) -> str:
    with Image.open(image_path) as im:
        w, h = im.size
    if w <= 0:
        return "zoom"
    if h / w > config.TALL_RATIO and (w / h) * config.HEIGHT <= config.WIDTH:
        # tall slice whose width, scaled to output width, still exceeds
        # output height -> vertical pan works
        with Image.open(image_path) as im:
            scaled_h = im.size[1] * config.WIDTH / im.size[0]
        if scaled_h > config.HEIGHT * 1.1:
            return "pan"
    return "zoom"


def render_shot(shot, index: int, build_dir: str) -> str:
    """Render one motion clip; return its relative filename."""
    dur = shot["end"] - shot["start"]
    frames = max(int(round(dur * config.FPS)), 1)
    out_name = f"clips/shot_{index:03d}.mp4"
    os.makedirs(os.path.join(build_dir, "clips"), exist_ok=True)
    img = os.path.relpath(shot["image"], build_dir)
    mode = _motion_mode(shot["image"])

    if mode == "pan":
        # scale to output width, pan top -> bottom over the shot
        vf = (
            f"scale={config.WIDTH}:-2,"
            f"crop={config.WIDTH}:{config.HEIGHT}:0:"
            f"'min(ih-{config.HEIGHT}, (ih-{config.HEIGHT})*t/{dur:.3f})'"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(config.FPS),
               "-t", f"{dur:.3f}", "-i", img, "-vf", vf,
               *config.VIDEO_CODEC, "-an", out_name]
    else:
        # cover-scale onto an oversized canvas, then Ken Burns zoom
        cw, ch = config.ZOOM_CANVAS_W, config.ZOOM_CANVAS_H
        zoom_in = index % 2 == 0
        if zoom_in:
            z = f"min(1+{config.ZOOM_STEP}*on,{config.ZOOM_MAX})"
        else:
            z = f"max({config.ZOOM_MAX}-{config.ZOOM_STEP}*on,1.0)"
        vf = (
            f"scale=w='if(gt(a,{cw}/{ch}),-1,{cw})':"
            f"h='if(gt(a,{cw}/{ch}),{ch},-1)',"
            f"crop={cw}:{ch},"
            f"zoompan=z='{z}':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={config.WIDTH}x{config.HEIGHT}:fps={config.FPS}"
        )
        cmd = ["ffmpeg", "-y", "-i", img, "-vf", vf,
               "-frames:v", str(frames), *config.VIDEO_CODEC, "-an", out_name]

    _run(cmd, cwd=build_dir)
    return out_name


def render_video(shots, beats, voice_path: str, build_dir: str) -> str:
    """Full render. Returns absolute path of the final MP4."""
    # extend the last shot slightly past the narration
    shots[-1]["end"] += config.TAIL_PAD_SEC

    clip_names = [render_shot(s, i, build_dir) for i, s in enumerate(shots)]

    concat_list = os.path.join(build_dir, "concat.txt")
    with open(concat_list, "w") as f:
        for name in clip_names:
            f.write(f"file '{name}'\n")

    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt",
          "-c", "copy", "silent.mp4"], cwd=build_dir)

    # Still write SRT for archival / Stage 1.5 use (can be loaded as sidecar)
    write_srt(beats, os.path.join(build_dir, "subs.srt"))
    voice = os.path.relpath(voice_path, build_dir)

    # Check if ffmpeg has subtitle-burning capability
    has_subs_filter = _has_filter("subtitles")
    has_drawtext = _has_filter("drawtext")

    if has_subs_filter:
        style = config.SUBTITLE_STYLE
        _run(["ffmpeg", "-y", "-i", "silent.mp4", "-i", voice,
              "-vf", f"subtitles=subs.srt:force_style='{style}'",
              "-map", "0:v", "-map", "1:a",
              *config.VIDEO_CODEC, *config.AUDIO_CODEC,
              "-shortest", "output.mp4"], cwd=build_dir)
    elif has_drawtext:
        # Build drawtext filter chain as fallback
        dt_parts = []
        for b in beats:
            text = b["text"].replace("\\", "\\\\").replace("'", "\u2019")
            text = text.replace(":", "\\:").replace("%", "%%")
            dt = (
                f"drawtext=text='{text}'"
                f":fontsize=32:fontcolor=white"
                f":borderw=2:bordercolor=black"
                f":x=(w-text_w)/2:y=h-60-text_h"
                f":enable='between(t\\,{b['start']:.3f}\\,{b['end']:.3f})'"
            )
            dt_parts.append(dt)
        vf = ",".join(dt_parts) if dt_parts else "null"
        _run(["ffmpeg", "-y", "-i", "silent.mp4", "-i", voice,
              "-vf", vf,
              "-map", "0:v", "-map", "1:a",
              *config.VIDEO_CODEC, *config.AUDIO_CODEC,
              "-shortest", "output.mp4"], cwd=build_dir)
    else:
        # No text rendering filters — mux audio only, subtitles as sidecar
        print("      [WARN] ffmpeg lacks subtitle/drawtext filters (needs "
              "libass or libfreetype).\n"
              "      Subtitles skipped — subs.srt written as sidecar.\n"
              "      Fix: brew reinstall ffmpeg  (homebrew default includes libass)")
        _run(["ffmpeg", "-y", "-i", "silent.mp4", "-i", voice,
              "-map", "0:v", "-map", "1:a",
              *config.VIDEO_CODEC, *config.AUDIO_CODEC,
              "-shortest", "output.mp4"], cwd=build_dir)

    return os.path.join(build_dir, "output.mp4")
