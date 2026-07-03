"""
Transcribe the voice track with faster-whisper and return word-level
timestamps. This is the timing backbone: every beat's start/end time
is derived from these words.

Fallback: if --no-whisper is passed (or faster-whisper is missing),
beats are timed proportionally by word count against the audio duration.
"""

import json
import subprocess


def audio_duration(voice_path: str) -> float:
    """Return audio duration in seconds via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", voice_path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def transcribe_words(voice_path: str):
    """
    Return a list of {"word": str, "start": float, "end": float},
    in narration order.
    """
    from faster_whisper import WhisperModel
    import config

    model = WhisperModel(
        config.WHISPER_MODEL,
        device=config.WHISPER_DEVICE,
        compute_type=config.WHISPER_COMPUTE,
    )
    segments, _info = model.transcribe(voice_path, word_timestamps=True)

    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({
                "word": w.word.strip(),
                "start": round(w.start, 3),
                "end": round(w.end, 3),
            })
    return words
