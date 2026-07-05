"""
Text-to-speech for narration beats using Google Cloud Chirp 3: HD, voice
en-US-Chirp3-HD-Charon. Each beat is voiced as ONE complete sentence so the
speech sounds natural. Beat *durations* come from the actual synthesized
audio length (not a guessed SRT), so image timing can be driven by real
speech.

Auth: set GOOGLE_APPLICATION_CREDENTIALS to a service-account key with the
Text-to-Speech API enabled (Application Default Credentials).
"""

import os
import re
import subprocess

VOICE_NAME = "en-US-Chirp3-HD-Charon"
LANGUAGE_CODE = "en-US"
SPEAKING_RATE = 1.0          # 0.25–2.0; 1.0 is natural. Lower = slower/calmer.
GAP_SEC = 0.35               # small breath between beats


def _clean_for_tts(text: str) -> str:
    """Remove bracketed cues like [music] and collapse whitespace."""
    text = re.sub(r"\[[^\]]*\]", "", text)
    return " ".join(text.split()).strip()


def synth_beat(text: str, out_path: str, client):
    """Synthesize one beat to an MP3. Skips if the file already exists
    (caching to save quota on rebuilds)."""
    from google.cloud import texttospeech
    if os.path.exists(out_path):
        return
    clean = _clean_for_tts(text)
    resp = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=clean),
        voice=texttospeech.VoiceSelectionParams(
            language_code=LANGUAGE_CODE, name=VOICE_NAME),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=SPEAKING_RATE),
    )
    with open(out_path, "wb") as f:
        f.write(resp.audio_content)


def audio_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def synth_all_beats(beats, tts_dir: str):
    """
    Synthesize every beat, then compute a real timeline from actual audio
    durations. Returns beats with 'start'/'end'/'audio' filled in, plus the
    ordered list of (audio_path, start_ms) for mixing.

    This is the key change: beat timing follows the SPEECH, so a long
    sentence gets a long on-screen hold and a short one gets a short hold —
    automatically, with no SRT guessing.
    """
    from google.cloud import texttospeech
    os.makedirs(tts_dir, exist_ok=True)
    client = texttospeech.TextToSpeechClient()

    t = 0.0
    timeline = []
    for b in beats:
        path = os.path.join(tts_dir, f"beat_{b['index']:03d}.mp3")
        synth_beat(b["text"], path, client)
        dur = audio_duration(path)
        b["audio"] = path
        b["start"] = round(t, 3)
        b["end"] = round(t + dur, 3)
        timeline.append((path, int(t * 1000)))
        t += dur + GAP_SEC
    return beats, timeline


def mix_voice_track(timeline, out_path: str):
    """Mix all beat MP3s onto one track, each delayed to its start time,
    using adelay + amix (normalize=0 so voice volume stays constant)."""
    inputs, filters, labels = [], [], []
    for i, (path, start_ms) in enumerate(timeline):
        inputs += ["-i", path]
        filters.append(f"[{i}:a]adelay={start_ms}|{start_ms}[a{i}]")
        labels.append(f"[a{i}]")
    filtergraph = ";".join(filters) + ";" + "".join(labels) + \
        f"amix=inputs={len(timeline)}:normalize=0[out]"
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filtergraph,
           "-map", "[out]", out_path]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
