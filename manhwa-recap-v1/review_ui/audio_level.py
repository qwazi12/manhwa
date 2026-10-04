"""Loudness for exports (LongForm lesson, owner plan step 7, 2026-10-04).

YouTube plays everything back at about -14 LUFS: a quieter upload just sounds
weak next to the videos around it, a louder one gets turned down. So every
export is normalised to EXPORT_LUFS (default -14) with ffmpeg's two-pass
`loudnorm` (measure, then apply the measured values linearly so the narration
keeps its dynamics). Video is stream-copied: nothing is re-encoded but audio.

Best-effort by design: if measuring or applying fails, the export keeps its
original audio and the result says why — loudness must never cost a video.
"""

import json
import os
import re
import subprocess

TP = -1.5          # true-peak ceiling (dBTP)
LRA = 11.0


def target():
    """EXPORT_LUFS env: a number (default -14) or 'off'."""
    v = (os.environ.get("EXPORT_LUFS") or "-14").strip().lower()
    if v in ("off", "none", "0"):
        return None
    try:
        return float(v)
    except ValueError:
        return -14.0


def measure(path, lufs=-14.0):
    """Pass 1: the file's integrated loudness and the values pass 2 needs."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
         "-af", f"loudnorm=I={lufs}:TP={TP}:LRA={LRA}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=900)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        raise RuntimeError("loudness could not be measured")
    return json.loads(m.group(0))


def normalise(path, lufs=None):
    """Rewrite `path` in place at the target loudness. Returns a report dict;
    never raises."""
    lufs = target() if lufs is None else lufs
    if lufs is None:
        return {"applied": False, "why": "EXPORT_LUFS=off"}
    try:
        m = measure(path, lufs)
        before = float(m["input_i"])
        if before == float("-inf") or m["input_i"] in ("-inf", "inf"):
            return {"applied": False, "why": "silent audio"}
        if abs(before - lufs) < 0.5:
            return {"applied": False, "before": before, "why": "already at target"}
        tmp = path + ".lufs.mp4"
        af = (f"loudnorm=I={lufs}:TP={TP}:LRA={LRA}:measured_I={m['input_i']}:"
              f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
              f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
        r = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-i", path, "-map", "0", "-c:v", "copy",
             "-af", af, "-ar", "48000", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp],
            capture_output=True, text=True, timeout=900)
        if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            if os.path.exists(tmp):
                os.remove(tmp)
            return {"applied": False, "before": before, "why": "ffmpeg failed: " + r.stderr[-200:]}
        os.replace(tmp, path)
        return {"applied": True, "before": round(before, 1), "target": lufs}
    except Exception as e:  # noqa — never cost the export
        return {"applied": False, "why": str(e)[:200]}
