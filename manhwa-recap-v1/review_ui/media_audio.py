"""Offline decoded-audio measurements. No synthesis, repairs or service calls.

Energy and spectral correspondence are technical evidence, not recognition of
spoken words. Thresholds are provisional until calibrated on approved media.
"""
import json
from functools import lru_cache
import os
import subprocess
import tempfile
import threading

import numpy as np

RATE = 16000
FRAME = 320  # 20 ms; bounded decoding memory, also used for export mapping
SILENCE_DB = -45.0
BANDS = 48


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise ValueError("media probe failed: " + r.stderr[-200:])
    return json.loads(r.stdout)


def duration(path):
    d = probe(path)
    return float(d.get("format", {}).get("duration") or 0)


def measure(path, timeout=900):
    """Decode all audio, retaining only 20 ms energy and normalized spectra.

    A timer kills a stalled decoder; stderr goes to a bounded-on-read temporary
    file so it cannot deadlock a full pipe. Original media is never written.
    """
    info = probe(path)
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise ValueError("audio stream is missing")
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
           "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]
    energy, spectra = [], []
    samples = clipped = 0
    peak = 0.0
    # Log-frequency bins make the signal comparison less sensitive to AAC,
    # loudness and atempo processing than direct sample equality.
    freqs = np.fft.rfftfreq(FRAME, 1 / RATE)
    bins = np.clip(np.searchsorted(np.geomspace(60, RATE / 2, BANDS), freqs), 0, BANDS - 1)
    window = np.hanning(FRAME)

    def consume(data):
        nonlocal samples, clipped, peak
        a = np.frombuffer(data, dtype="<f4")
        if not len(a):
            return
        if not np.isfinite(a).all():
            raise ValueError("decoded audio contains non-finite samples")
        samples += len(a)
        peak = max(peak, float(np.max(np.abs(a))))
        clipped += int(np.count_nonzero(np.abs(a) >= 0.999))
        for off in range(0, len(a), FRAME):
            f = a[off:off + FRAME]
            energy.append(float(np.sqrt(np.mean(f * f))))
            if len(f) < FRAME:
                f = np.pad(f, (0, FRAME - len(f)))
            power = np.bincount(bins, weights=np.abs(np.fft.rfft(f * window)), minlength=BANDS)
            power /= max(float(np.linalg.norm(power)), 1e-12)
            spectra.append(power.astype(np.float32))

    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
        timer = threading.Timer(timeout, proc.kill)
        timer.daemon = True
        timer.start()
        try:
            while True:
                block = proc.stdout.read(FRAME * 4 * 64)
                if not block:
                    break
                consume(block)
            code = proc.wait()
            errors.seek(0)
            error = errors.read(2048).decode(errors="replace")
            if code or error.strip():
                raise ValueError("audio decode failed: " + (error[-200:] or f"exit {code}"))
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
    if not samples:
        raise ValueError("audio decodes to zero samples")
    rms = np.asarray(energy)
    active = rms >= 10 ** (SILENCE_DB / 20)
    total = samples / RATE
    intervals, start = [], None
    for i, is_active in enumerate(np.r_[active, True]):
        if not is_active and start is None:
            start = i * FRAME / RATE
        if is_active and start is not None:
            end = min(total, i * FRAME / RATE)
            if end - start >= 0.199:
                intervals.append([round(start, 3), round(end, 3)])
            start = None
    return {"duration": total, "active_seconds": min(total, int(active.sum()) * FRAME / RATE),
            "peak": peak, "clipped_fraction": clipped / samples, "silences": intervals,
            "leading_silence": intervals[0][1] if intervals and intervals[0][0] == 0 else 0,
            "trailing_silence": total - intervals[-1][0] if intervals and abs(intervals[-1][1] - total) < .03 else 0,
            "rms": rms, "spectra": np.asarray(spectra), "active": active}


def public_metrics(m):
    return {k: v for k, v in m.items() if k not in ("rms", "spectra", "active")}


@lru_cache(maxsize=4096)
def _health(path, signature):
    return public_metrics(measure(path))


def health(path):
    st = os.stat(path)
    return _health(os.path.realpath(path), (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns))


def correspondence(reference, delivered, start, speed=1.0):
    """Fraction of expected active frames supported by final audio spectra.

    Uses a small local timing tolerance for codec/atempo shifts. Music energy
    alone is insufficient. This is NOT ASR and cannot prove word correctness.
    """
    active = np.flatnonzero(reference["active"])
    if not len(active):
        return 0.0
    # Stay away from codec/atempo edges where short transient windows differ.
    if len(active) > 12:
        active = active[3:-3]
    expected = reference["spectra"][active]
    target = np.rint((start + active * FRAME / RATE / speed) * RATE / FRAME).astype(int)
    best = np.zeros(len(active))
    for delta in range(-3, 4):
        idx = target + delta
        valid = (idx >= 0) & (idx < len(delivered["spectra"]))
        values = np.zeros(len(active))
        values[valid] = np.sum(expected[valid] * delivered["spectra"][idx[valid]], axis=1)
        values[valid] *= delivered["active"][idx[valid]]
        best = np.maximum(best, values)
    return float(np.mean(best >= .65))
