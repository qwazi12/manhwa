"""
The core mechanic under test:

1. Split the written script into narration beats.
2. Time each beat against the voice track (whisper words, or proportional).
3. Assign chapter images to beats chronologically (chronology-first
   alignment: image order follows story order, so beat N of the narration
   maps to the corresponding stretch of images).

Outputs plain dicts that main.py writes as beats.json / shots.json /
timeline.json — these are the Stage 1.5 data contracts in embryo.
"""

import re
import config


# ---------------------------------------------------------------- beats

def split_beats(script_text: str):
    """
    One beat per non-empty line. If the script is a single block of prose,
    fall back to sentence splitting.
    """
    lines = [ln.strip() for ln in script_text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        beats = lines
    else:
        beats = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script_text.strip())
                 if s.strip()]
    return [{"index": i, "text": t, "word_count": len(t.split())}
            for i, t in enumerate(beats)]


def time_beats_with_words(beats, words):
    """
    Map beats onto whisper word timestamps by cumulative word-count
    proportion. Assumes the narrator reads the script roughly verbatim
    (Stage 1 assumption — you recorded your own script).
    """
    total_script_words = sum(b["word_count"]) if False else sum(
        b["word_count"] for b in beats)
    n_audio_words = len(words)
    if n_audio_words == 0:
        raise ValueError("Whisper returned no words. Check the audio file.")

    cursor = 0  # running script word count
    for b in beats:
        start_idx = min(round(cursor / total_script_words * n_audio_words),
                        n_audio_words - 1)
        cursor += b["word_count"]
        end_idx = min(round(cursor / total_script_words * n_audio_words) - 1,
                      n_audio_words - 1)
        end_idx = max(end_idx, start_idx)
        b["start"] = words[start_idx]["start"]
        b["end"] = words[end_idx]["end"]

    # enforce monotonic, gap-free timing
    for i in range(1, len(beats)):
        beats[i]["start"] = max(beats[i]["start"], beats[i - 1]["end"])
        beats[i]["end"] = max(beats[i]["end"], beats[i]["start"] + 0.2)
    return beats


def time_beats_proportional(beats, duration: float):
    """Fallback timing: spread beats over the audio by word count."""
    total = sum(b["word_count"] for b in beats)
    t = 0.0
    for b in beats:
        span = duration * b["word_count"] / total
        b["start"], b["end"] = round(t, 3), round(t + span, 3)
        t += span
    return beats


# ---------------------------------------------------------------- shots

def assign_shots(beats, image_paths):
    """
    Chronology-first assignment. Two cases:

    * images >= beats: every beat gets >=1 image (largest-remainder split
      weighted by beat duration); a beat with m images is cut into m
      equal-duration shots.
    * images < beats: consecutive beats share an image; the shot spans
      the full run of beats it covers.

    Returns a flat, ordered shot list.
    """
    n_img, n_beat = len(image_paths), len(beats)
    shots = []

    if n_img >= n_beat:
        durations = [b["end"] - b["start"] for b in beats]
        total = sum(durations)
        # largest-remainder apportionment, minimum 1 image per beat
        quotas = [max(1, d / total * n_img) for d in durations]
        counts = [int(q) for q in quotas]
        while sum(counts) < n_img:
            rems = [(quotas[i] - counts[i], i) for i in range(n_beat)]
            rems.sort(reverse=True)
            counts[rems[0][1]] += 1
        while sum(counts) > n_img:
            rems = [(quotas[i] - counts[i], i) for i in range(n_beat)
                    if counts[i] > 1]
            rems.sort()
            counts[rems[0][1]] -= 1

        img_i = 0
        for b, m in zip(beats, counts):
            span = (b["end"] - b["start"]) / m
            for k in range(m):
                shots.append({
                    "image": image_paths[img_i],
                    "start": round(b["start"] + k * span, 3),
                    "end": round(b["start"] + (k + 1) * span, 3),
                    "beat_indices": [b["index"]],
                })
                img_i += 1
    else:
        # map each beat midpoint to an image index; group consecutive beats
        for b in beats:
            frac = (b["index"] + 0.5) / n_beat
            img_i = min(int(frac * n_img), n_img - 1)
            if shots and shots[-1]["image"] == image_paths[img_i]:
                shots[-1]["end"] = b["end"]
                shots[-1]["beat_indices"].append(b["index"])
            else:
                shots.append({
                    "image": image_paths[img_i],
                    "start": b["start"],
                    "end": b["end"],
                    "beat_indices": [b["index"]],
                })

    return _merge_short_shots(shots)


def _merge_short_shots(shots):
    """Merge shots shorter than MIN_SHOT_SEC into the previous shot."""
    merged = []
    for s in shots:
        if merged and (s["end"] - s["start"]) < config.MIN_SHOT_SEC:
            merged[-1]["end"] = s["end"]
            merged[-1]["beat_indices"] = sorted(
                set(merged[-1]["beat_indices"] + s["beat_indices"]))
        else:
            merged.append(s)
    # first shot could also be too short — merge forward if needed
    if len(merged) >= 2 and (merged[0]["end"] - merged[0]["start"]) < config.MIN_SHOT_SEC:
        merged[1]["start"] = merged[0]["start"]
        merged[1]["beat_indices"] = sorted(
            set(merged[0]["beat_indices"] + merged[1]["beat_indices"]))
        merged.pop(0)
    return merged
