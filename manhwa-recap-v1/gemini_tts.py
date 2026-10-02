"""Narration voice: Gemini 3.8 Flash TTS (default for new projects) or the
legacy Google Cloud Chirp voice (projects voiced before the switch).

Each project is PINNED to the voice it was first voiced with (tts.json in the
project dir). A chapter voiced by Chirp keeps Chirp for every later edit, so a
video never changes narrator half-way; chapters ingested from 2026-10-02 on
get Gemini Charon.
"""
import base64
import json
import os
import random
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
_TTS_CACHE_DIR = os.path.join(HERE, "projects", "_ttscache")

# Default voice & model
DEFAULT_VOICE = "Charon"  # Prebuilt studio voice in Gemini 3.8 Flash TTS
DEFAULT_STYLE = "dramatic, engaging manhwa recap narrator"
DEFAULT_MODEL = "gemini-3.8-flash-tts"

PIN_FILE = "tts.json"
CHIRP_MODEL = "chirp3-hd-charon"
CHIRP_VOICE = "en-US-Chirp3-HD-Charon"

# Transient failures (429 rate limit, 5xx, timeouts) are retried, bounded,
# with exponential backoff and jitter. Voice lines are recorded several at a
# time during an ingest, and one unretried 429 used to fail the whole job.
TTS_RETRIES = int(os.environ.get("TTS_RETRIES", "4"))


def env_any_case(name):
    v = os.environ.get(name)
    if v:
        return v
    want = name.lower()
    for k, val in os.environ.items():
        if k.lower() == want and val:
            return val
    return None


def get_tts_engine_config():
    """The voice a NEW project gets, from the environment."""
    gemini_key = env_any_case("GEMINI_API_KEY")
    tts_key = env_any_case("TTS_API_KEY")
    model = env_any_case("TTS_MODEL") or DEFAULT_MODEL
    voice = env_any_case("TTS_VOICE") or DEFAULT_VOICE
    style = env_any_case("TTS_STYLE") or DEFAULT_STYLE

    # If GEMINI_API_KEY is available (or TTS_MODEL explicitly specifies gemini), use Gemini 3.8 TTS
    if gemini_key and (model.startswith("gemini") or not tts_key):
        return {
            "provider": "gemini",
            "api_key": gemini_key,
            "model": model,
            "voice": voice,
            "style": style,
        }
    elif tts_key:
        return _chirp_cfg(tts_key)
    return {
        "provider": "none",
        "api_key": None,
        "model": model,
        "voice": voice,
        "style": style,
    }


def _chirp_cfg(key):
    return {"provider": "chirp", "api_key": key, "model": CHIRP_MODEL,
            "voice": CHIRP_VOICE, "style": ""}


def _has_voiced_audio(pdir):
    adir = os.path.join(pdir, "audio")
    try:
        return any(f.endswith(".mp3") for f in os.listdir(adir))
    except OSError:
        return False


def engine_for_project(pdir):
    """The voice this project is pinned to, with the current key for it.

    - tts.json present: that voice.
    - no tts.json but audio already recorded: voiced before the switch, so the
      legacy Chirp voice (pinned now, so the answer never changes).
    - neither: a new project; it gets the current default and is pinned.
    Raises with the missing variable's name if that voice's key is absent.
    """
    rec = None
    path = os.path.join(pdir, PIN_FILE) if pdir else None
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            rec = None
    if rec is None:
        if pdir and _has_voiced_audio(pdir):
            rec = {"provider": "chirp", "model": CHIRP_MODEL, "voice": CHIRP_VOICE,
                   "style": "", "reason": "voiced before the Gemini switch"}
        else:
            cur = get_tts_engine_config()
            rec = {k: cur[k] for k in ("provider", "model", "voice", "style")}
        rec["pinned_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if path and rec["provider"] != "none":
            try:
                os.makedirs(pdir, exist_ok=True)
                tmp = "%s.%d.tmp" % (path, os.getpid())
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(rec, f, indent=1)
                os.replace(tmp, path)
            except OSError:
                pass

    if rec.get("provider") == "chirp":
        key = env_any_case("TTS_API_KEY")
        if not key:
            raise RuntimeError("TTS_API_KEY is not set, and this project is "
                               "pinned to the legacy Chirp voice")
        return _chirp_cfg(key)
    if rec.get("provider") == "gemini":
        key = env_any_case("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set, and this project is "
                               "pinned to the Gemini voice")
        return {"provider": "gemini", "api_key": key,
                "model": rec.get("model") or DEFAULT_MODEL,
                "voice": rec.get("voice") or DEFAULT_VOICE,
                "style": rec.get("style", DEFAULT_STYLE)}
    raise RuntimeError("No narration voice is configured: set GEMINI_API_KEY "
                       "(or TTS_API_KEY for the legacy voice)")


def _transient(e):
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 429 or e.code >= 500
    if isinstance(e, (TimeoutError, ConnectionError, urllib.error.URLError)):
        return True
    msg = str(e).lower()
    return any(k in msg for k in ("timed out", "resource_exhausted", "unavailable"))


def _post_with_retry(req, ctx, retries=None, _sleep=time.sleep, _open=None):
    retries = TTS_RETRIES if retries is None else retries
    opener = _open or (lambda r: urllib.request.urlopen(r, context=ctx, timeout=60))
    for attempt in range(retries + 1):
        try:
            with opener(req) as r:
                return json.load(r)
        except Exception as e:
            if attempt == retries or not _transient(e):
                raise
            _sleep(min(30, 2 ** attempt * 2) + random.uniform(0, 1))


def _wav_seconds(raw):
    try:
        import io
        with wave.open(io.BytesIO(raw)) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


def synth_gemini_tts(text, out_path, cfg=None, style=None, voice=None, _open=None):
    """Record one line. Returns {"seconds", "prompt_tokens", "audio_tokens"}
    so the caller can bill it at Google's audio-token rate."""
    cfg = cfg or get_tts_engine_config()
    api_key = cfg.get("api_key")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured for Gemini TTS")

    model = cfg.get("model") or DEFAULT_MODEL
    chosen_voice = voice or cfg.get("voice") or DEFAULT_VOICE
    chosen_style = style if style is not None else cfg.get("style", DEFAULT_STYLE)

    body = {
        "model": model,
        "input": [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": text,
                "annotations": [{
                    "type": "speech_metadata",
                    "style": chosen_style
                }] if chosen_style else []
            }]
        }],
        "response_format": {"type": "audio"},
        "generation_config": {
            "speech_config": [
                {"voice": chosen_voice}
            ]
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/interactions?key={api_key}"
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    resp = _post_with_retry(req, ctx, _open=_open)

    audio_b64 = None
    for step in resp.get("steps") or []:
        for content in step.get("content") or []:
            if content.get("type") == "audio":
                audio_b64 = content.get("data")

    if not audio_b64:
        raise RuntimeError(f"Gemini TTS returned no audio data: {json.dumps(resp)[:300]}")

    raw_wav = base64.b64decode(audio_b64)
    seconds = _wav_seconds(raw_wav)

    # If target is mp3, transcode via ffmpeg
    if out_path.endswith(".mp3"):
        tmp_wav = f"{out_path}.tmp.wav"
        with open(tmp_wav, "wb") as f:
            f.write(raw_wav)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_wav, "-acodec", "libmp3lame", "-b:a", "192k", out_path],
                check=True, capture_output=True
            )
        finally:
            if os.path.exists(tmp_wav):
                os.remove(tmp_wav)
    else:
        with open(out_path, "wb") as f:
            f.write(raw_wav)

    um = resp.get("usage") or resp.get("usageMetadata") or {}
    prompt_tokens = (um.get("input_tokens") or um.get("promptTokenCount")
                     or um.get("total_input_tokens") or max(1, len(text) // 4))
    # Audio is billed at 25 tokens per second of output (Google rate card).
    audio_tokens = int(round((seconds or len(text) / 12.0) * 25))
    return {"seconds": seconds, "prompt_tokens": int(prompt_tokens),
            "audio_tokens": audio_tokens}
