#!/usr/bin/env python3
"""
Full-chapter validation driver: segment -> TTS (REST) -> timeline -> matcher.

Synthesizes narration via the Google TTS REST endpoint + API key (bypasses the
google.cloud SDK, whose import hangs in this venv, and works with the API key
in .env rather than a service account). MP3s are cached, so re-runs are free.

Outputs (in build_test/):
    beats_full.json      — 127 beats with real start/end from audio duration
    beatsheet_full.json  — matcher assignments over the full chapter
    tts_full/beat_NNN.mp3 — one clip per beat
"""
import base64
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request

import beat_segmenter
import matcher

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "input", "full_script.txt")
TTS_DIR = os.path.join(HERE, "build_test", "tts_full")
GAP_SEC = 0.35  # matches tts.py

VOICE = "en-US-Chirp3-HD-Charon"
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl._create_unverified_context()


def _api_key():
    for line in open(os.path.join(ROOT, ".env")):
        if line.startswith("TTS_API_KEY"):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TTS_API_KEY not in .env")


def synth(text, out_path, key):
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}"
    body = {"input": {"text": text},
            "voice": {"languageCode": "en-US", "name": VOICE},
            "audioConfig": {"audioEncoding": "MP3"}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    for attempt in range(4):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=60, context=_CTX))
            open(out_path, "wb").write(base64.b64decode(d["audioContent"]))
            return
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1)); continue
            raise SystemExit(f"TTS HTTP {e.code}: {e.read().decode()[:200]}")


def dur(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], text=True).strip()
    return round(float(out), 3)


def main():
    key = _api_key()
    os.makedirs(TTS_DIR, exist_ok=True)
    beats = beat_segmenter.segment_beats(open(SCRIPT).read())
    print(f"Segmented {len(beats)} beats. Synthesizing (cached)...")

    t = 0.0
    for b in beats:
        p = os.path.join(TTS_DIR, f"beat_{b['index']:03d}.mp3")
        synth(b["text"], p, key)
        d = dur(p)
        b["start"], b["end"], b["audio"] = round(t, 3), round(t + d, 3), p
        t += d + GAP_SEC
        if b["index"] % 20 == 0:
            print(f"  beat {b['index']}/{len(beats)}  t={t:.1f}s")

    json.dump(beats, open(os.path.join(HERE, "build_test", "beats_full.json"), "w"),
              indent=2, ensure_ascii=False)
    print(f"Total narration timeline: {t:.1f}s ({t/60:.1f} min)")

    panels = json.load(open(os.path.join(ROOT, "panel-describe", "descriptions.json")))
    for p in panels:
        if p.get("file") and not os.path.isabs(p["file"]):
            p["file"] = os.path.join(ROOT, "panel-describe", p["file"])
    panels = [p for p in panels if p.get("ok", True) and p.get("width") and p.get("height")]

    assignments, method = matcher.match_beats_to_panels(beats, panels)
    shots = matcher.build_timeline(beats, panels, assignments)
    json.dump(shots, open(os.path.join(HERE, "build_test", "beatsheet_full.json"), "w"),
              indent=2, ensure_ascii=False)

    uniq = len({s["panel_id"] for s in shots})
    held = sum(1 for s in shots if s["held"])
    print(f"Matcher ({method}): {len(shots)} beats -> {uniq} distinct panels, "
          f"{held} held / {len(shots)-held} advanced.")


if __name__ == "__main__":
    main()
