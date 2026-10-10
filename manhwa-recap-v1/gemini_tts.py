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
CHOICE_FILE = "tts_choice.json"       # the voice picked for this chapter at ingest
DEFAULT_FILE = "_voice_default.json"  # studio default, in the projects root

# Gemini TTS prebuilt voices (Google's published names, each confirmed to be
# accepted by gemini-3.8-flash-tts) with Google's one-word character notes.
GEMINI_VOICES = [
    ("Charon", "informative"), ("Algenib", "gravelly"), ("Gacrux", "mature"),
    ("Orus", "firm"), ("Alnilam", "firm"), ("Kore", "firm"), ("Fenrir", "excitable"),
    ("Puck", "upbeat"), ("Iapetus", "clear"), ("Erinome", "clear"),
    ("Rasalgethi", "informative"), ("Sadaltager", "knowledgeable"),
    ("Schedar", "even"), ("Algieba", "smooth"), ("Despina", "smooth"),
    ("Enceladus", "breathy"), ("Umbriel", "easy-going"), ("Callirrhoe", "easy-going"),
    ("Zubenelgenubi", "casual"), ("Achird", "friendly"), ("Sulafat", "warm"),
    ("Vindemiatrix", "gentle"), ("Achernar", "soft"), ("Aoede", "breezy"),
    ("Zephyr", "bright"), ("Autonoe", "bright"), ("Leda", "youthful"),
    ("Laomedeia", "upbeat"), ("Sadachbia", "lively"), ("Pulcherrima", "forward"),
]
STYLE_PRESETS = [
    ("", "Natural (no style direction)"),
    ("dramatic, engaging manhwa recap narrator", "Dramatic recap narrator"),
    ("calm, clear storyteller, steady pace", "Calm storyteller"),
    ("deep, cinematic trailer narrator, measured and intense", "Cinematic trailer"),
    ("energetic, fast-paced YouTube recap host", "Energetic recap host"),
]
VOICE_NAMES = {v for v, _ in GEMINI_VOICES}


def voice_options():
    """Every voice the operator can pick, as ids the UI and API share."""
    out = [{"id": "chirp:Charon", "provider": "chirp", "voice": "Charon",
            "label": "Charon (classic) — Google Chirp 3 HD",
            "desc": "the voice of every chapter made before 2026-10-02"}]
    for v, d in GEMINI_VOICES:
        out.append({"id": "gemini:" + v, "provider": "gemini", "voice": v,
                    "label": f"{v} — {d}", "desc": "Gemini 3.8 Flash TTS"})
    return out


def parse_choice(voice_id, style=None):
    """'gemini:Puck' / 'chirp:Charon' -> a pin record, or ValueError."""
    prov, _, voice = (voice_id or "").partition(":")
    if prov == "chirp" and voice in ("", "Charon"):
        return {"provider": "chirp", "model": CHIRP_MODEL, "voice": CHIRP_VOICE, "style": ""}
    if prov == "gemini" and voice in VOICE_NAMES:
        st = (style if style is not None else DEFAULT_STYLE) or ""
        if len(st) > 200:
            raise ValueError("style is too long (200 characters at most)")
        return {"provider": "gemini",
                "model": env_any_case("TTS_MODEL") or DEFAULT_MODEL,
                "voice": voice, "style": st.strip()}
    raise ValueError(f"unknown voice {voice_id!r}")


def choice_id(rec):
    if not rec:
        return None
    return "chirp:Charon" if rec.get("provider") == "chirp" else "gemini:" + str(rec.get("voice"))


def load_default(root):
    try:
        with open(os.path.join(root, DEFAULT_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def save_default(root, voice_id, style=None):
    rec = parse_choice(voice_id, style)
    rec["set_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_json(os.path.join(root, DEFAULT_FILE), rec)
    return rec


def save_choice(pdir, voice_id, style=None):
    """The voice picked for one chapter at ingest. It applies when the chapter
    is first voiced (or on a fresh re-ingest); a chapter that already has a
    voice keeps it, so narrators never mix within a video."""
    rec = parse_choice(voice_id, style)
    _write_json(os.path.join(pdir, CHOICE_FILE), rec)
    return rec
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


def get_tts_engine_config(root=None):
    """The voice a NEW project gets: the studio default saved from the UI
    (root/_voice_default.json) when there is one, else the environment."""
    d = load_default(root) if root else None
    if d and d.get("provider") == "chirp" and env_any_case("TTS_API_KEY"):
        return _chirp_cfg(env_any_case("TTS_API_KEY"))
    if d and d.get("provider") == "gemini" and env_any_case("GEMINI_API_KEY"):
        return {"provider": "gemini", "api_key": env_any_case("GEMINI_API_KEY"),
                "model": d.get("model") or DEFAULT_MODEL,
                "voice": d.get("voice") or DEFAULT_VOICE,
                "style": d.get("style", DEFAULT_STYLE)}
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
            picked = None
            try:
                with open(os.path.join(pdir, CHOICE_FILE), encoding="utf-8") as f:
                    picked = json.load(f)
            except (OSError, ValueError, TypeError):
                picked = None
            if picked and picked.get("provider") in ("gemini", "chirp"):
                rec = {k: picked.get(k, "") for k in ("provider", "model", "voice", "style")}
                rec["reason"] = "picked at ingest"
            else:
                cur = get_tts_engine_config(os.path.dirname(pdir.rstrip("/")) if pdir else None)
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
    if not text or not text.strip():
        silence_sec = 0.5
        if out_path.endswith(".mp3"):
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(silence_sec),
                 "-acodec", "libmp3lame", "-b:a", "192k", out_path],
                check=True, capture_output=True
            )
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(silence_sec),
                 out_path],
                check=True, capture_output=True
            )
        return {"seconds": silence_sec, "prompt_tokens": 0, "audio_tokens": 0}
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

    try:
        resp = _post_with_retry(req, ctx, _open=_open)
    except Exception as e:
        err_body = ""
        if hasattr(e, "read"):
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
        print(f"[Gemini TTS FAIL] text={repr(text[:100])} error={e} body={err_body}", flush=True)
        if chosen_style:
            try:
                plain_body = {
                    "model": model,
                    "input": [{"type": "user_input", "content": [{"type": "text", "text": text}]}],
                    "response_format": {"type": "audio"},
                    "generation_config": {"speech_config": [{"voice": chosen_voice}]}
                }
                plain_req = urllib.request.Request(
                    url, data=json.dumps(plain_body).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                resp = _post_with_retry(plain_req, ctx, _open=_open)
            except Exception as e2:
                print(f"[Gemini TTS plain retry failed: {e2}] -> fallback to local silence", flush=True)
                return _generate_silence(out_path, 0.5)
        else:
            print("[Gemini TTS fallback to local silence]", flush=True)
            return _generate_silence(out_path, 0.5)

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
