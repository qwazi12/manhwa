"""Join rendered shots without accumulating AAC encoder padding.

Keep the encoded pictures. Decode each soundtrack independently, fit it to
that shot's video frames, then encode the joined soundtrack once. Packet-copy
concatenation of independently encoded AAC tracks repeats priming/padding at
every boundary and can drift seconds across an episode.
"""
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile

RATE = 48000
CHANNELS = 2


def _run(args, **kwargs):
    return subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *map(str, args)],
                          check=True, stderr=subprocess.PIPE, timeout=900, **kwargs)


def _shot(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True, timeout=30)
    streams = json.loads(result.stdout)["streams"]
    video = next(s for s in streams if s.get("codec_type") == "video")
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise ValueError(f"Rendered shot has no soundtrack: {path.name}")
    duration = Fraction(video["duration_ts"]) * Fraction(video["time_base"])
    if duration <= 0 or abs(float(video.get("start_time", 0))) > .001:
        raise ValueError(f"Rendered shot must have positive duration and start at zero: {path.name}")
    signature = tuple(video.get(k) for k in
                      ("codec_name", "width", "height", "pix_fmt", "time_base", "r_frame_rate"))
    return duration, signature


def concatenate(entries, destination):
    """Atomically assemble stream-matched shots; inputs are never modified."""
    paths = [Path(p).resolve(strict=True) for p in entries]
    if not paths:
        raise ValueError("No rendered shots to concatenate")
    destination = Path(destination)
    if destination.resolve() in paths:
        raise ValueError("The export must not replace an input shot")
    shots = [_shot(p) for p in paths]
    if any(s[1] != shots[0][1] for s in shots):
        raise ValueError("Rendered shot video streams do not match")
    samples = [round(duration * RATE) for duration, _ in shots]
    total = sum((duration for duration, _ in shots), Fraction())
    # Temp data lives with the export, inside the caller's existing sandbox.
    with tempfile.TemporaryDirectory(prefix=".concat-", dir=destination.parent) as work:
        work = Path(work)
        listing = work / "pictures.ffconcat"
        listing.write_text("ffconcat version 1.0\n" + "".join(
            "file '" + str(p).replace("'", "'\\''") + "'\n"
            + f"duration {float(duration):.12f}\n"
            for p, (duration, _) in zip(paths, shots)))
        pcm = work / "soundtrack.f32le"
        # Bounded batches avoid holding hundreds of demuxers/file descriptors.
        with pcm.open("wb") as output:
            for begin in range(0, len(paths), 16):
                batch = paths[begin:begin + 16]
                inputs = [a for p in batch for a in ("-i", p)]
                filters = [f"[{i}:a:0]aresample={RATE},aformat=channel_layouts=stereo,"
                           f"apad,atrim=end_sample={samples[begin+i]},asetpts=PTS-STARTPTS[a{i}]"
                           for i in range(len(batch))]
                filters.append("".join(f"[a{i}]" for i in range(len(batch)))
                               + f"concat=n={len(batch)}:v=0:a=1[out]")
                _run([*inputs, "-filter_complex", ";".join(filters), "-map", "[out]",
                      "-ar", RATE, "-ac", CHANNELS, "-f", "f32le", "pipe:1"], stdout=output)
        if pcm.stat().st_size != sum(samples) * CHANNELS * 4:
            raise ValueError("Decoded soundtrack did not fit the delivered shot schedule")
        final = work / "assembled.mp4"
        _run(["-copyts", "-f", "concat", "-safe", "0", "-i", listing,
              "-f", "f32le", "-ar", RATE, "-ac", CHANNELS, "-i", pcm,
              "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
              "-c:a", "aac", "-b:a", "192k", "-t", f"{float(total):.12f}",
              "-movflags", "+faststart", final], stdout=subprocess.DEVNULL)
        final.replace(destination)
    return str(destination)
