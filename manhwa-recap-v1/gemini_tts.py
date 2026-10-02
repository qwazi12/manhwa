
import base64
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_TTS_CACHE_DIR = os.path.join(HERE, "projects", "_ttscache")

# Default voice & model
DEFAULT_VOICE = "Charon"  # Prebuilt studio voice in Gemini 3.8 Flash TTS
DEFAULT_STYLE = "dramatic, engaging manhwa recap narrator"
DEFAULT_MODEL = "gemini-3.8-flash-tts"

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
        return {
            "provider": "chirp",
            "api_key": tts_key,
            "model": "chirp3-hd-charon",
            "voice": "en-US-Chirp3-HD-Charon",
            "style": "",
        }
    return {
        "provider": "none",
        "api_key": None,
        "model": model,
        "voice": voice,
        "style": style,
    }

def synth_gemini_tts(text, out_path, cfg=None, style=None, voice=None):
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

    with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
        resp = json.load(r)

    audio_b64 = None
    for step in resp.get("steps") or []:
        for content in step.get("content") or []:
            if content.get("type") == "audio":
                audio_b64 = content.get("data")

    if not audio_b64:
        raise RuntimeError(f"Gemini TTS returned no audio data: {json.dumps(resp)[:300]}")

    raw_wav = base64.b64decode(audio_b64)

    # If target is mp3, transcode losslessly via ffmpeg
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

    return out_path
