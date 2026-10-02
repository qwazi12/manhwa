"""Narration voice wiring: Gemini 3.8 Flash TTS for new projects, the legacy
Chirp voice pinned for projects voiced before the switch, retries, atomic
cache, back-compatible Chirp cache key, and audio-token pricing.
No network: every HTTP call and the usage gate are mocked."""
import base64
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import gemini_tts  # noqa: E402

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def _wav(seconds=2.0, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class _Resp:
    def __init__(self, data):
        self._b = json.dumps(data).encode()

    def read(self, *a):
        return self._b

    def __enter__(self):
        return io.BytesIO(self._b)

    def __exit__(self, *a):
        return False


def main():
    saved_env = {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "TTS_API_KEY",
                                                 "TTS_MODEL", "TTS_VOICE", "TTS_STYLE")}
    for k in saved_env:
        os.environ.pop(k, None)
    try:
        # ---- default resolution
        os.environ["GEMINI_API_KEY"] = "mock_gemini"
        os.environ["TTS_API_KEY"] = "mock_chirp"
        cfg = gemini_tts.get_tts_engine_config()
        check("new projects default to Gemini 3.8 Flash TTS, Charon",
              cfg["provider"] == "gemini" and cfg["model"] == "gemini-3.8-flash-tts"
              and cfg["voice"] == "Charon")
        os.environ.pop("GEMINI_API_KEY")
        check("without GEMINI_API_KEY the default falls back to Chirp",
              gemini_tts.get_tts_engine_config()["provider"] == "chirp")
        os.environ["GEMINI_API_KEY"] = "mock_gemini"

        # ---- per-project pinning
        old = tempfile.mkdtemp(prefix="ttsold_")
        os.makedirs(os.path.join(old, "audio"))
        open(os.path.join(old, "audio", "beat_000.mp3"), "wb").write(b"x")
        e = gemini_tts.engine_for_project(old)
        check("a project voiced before the switch stays on the Chirp voice",
              e["provider"] == "chirp")
        pin = json.load(open(os.path.join(old, "tts.json")))
        check("...and is pinned in tts.json", pin["provider"] == "chirp")

        new = tempfile.mkdtemp(prefix="ttsnew_")
        e = gemini_tts.engine_for_project(new)
        check("a new project gets Gemini Charon and is pinned",
              e["provider"] == "gemini"
              and json.load(open(os.path.join(new, "tts.json")))["voice"] == "Charon")
        os.makedirs(os.path.join(new, "audio"))
        open(os.path.join(new, "audio", "beat_000.mp3"), "wb").write(b"x")
        check("once pinned to Gemini, recorded audio does not flip it to Chirp",
              gemini_tts.engine_for_project(new)["provider"] == "gemini")
        os.environ.pop("TTS_API_KEY")
        try:
            gemini_tts.engine_for_project(old)
            check("a Chirp-pinned project without TTS_API_KEY fails loudly", False)
        except RuntimeError as ex:
            check("a Chirp-pinned project without TTS_API_KEY fails loudly",
                  "TTS_API_KEY" in str(ex))
        os.environ["TTS_API_KEY"] = "mock_chirp"

        # ---- the request, retries, transcode, token count
        sent, calls = {}, {"n": 0}
        audio = base64.b64encode(_wav(2.0)).decode()

        def opener_429_then_ok(req):
            calls["n"] += 1
            sent["body"] = json.loads(req.data)
            if calls["n"] == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            return _Resp({"steps": [{"content": [{"type": "audio", "data": audio}]}]})
        orig_sleep = gemini_tts.time.sleep
        gemini_tts.time.sleep = lambda s: None
        out = os.path.join(tempfile.mkdtemp(), "beat.mp3")
        info = gemini_tts.synth_gemini_tts(
            "Yu Shin kept his head down.", out,
            cfg=gemini_tts.engine_for_project(new), _open=opener_429_then_ok)
        b = sent["body"]
        check("a 429 is retried, then the line is recorded", calls["n"] == 2)
        check("the transcript is sent verbatim",
              b["input"][0]["content"][0]["text"] == "Yu Shin kept his head down.")
        check("the style rides in speech_metadata, not in the text",
              b["input"][0]["content"][0]["annotations"][0]["type"] == "speech_metadata")
        check("voice Charon is requested",
              b["generation_config"]["speech_config"][0]["voice"] == "Charon")
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", out], capture_output=True, text=True)
        check("the WAV is transcoded to a playable MP3",
              os.path.exists(out) and abs(float(p.stdout.strip() or 0) - 2.0) < 0.2)
        check("audio tokens are counted at 25 per second", info["audio_tokens"] == 50)

        calls["n"] = 0

        def opener_400(req):
            calls["n"] += 1
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)
        try:
            gemini_tts.synth_gemini_tts("x", out + "2.mp3",
                                        cfg=gemini_tts.engine_for_project(new), _open=opener_400)
        except urllib.error.HTTPError:
            pass
        check("a 400 is not retried", calls["n"] == 1)
        gemini_tts.time.sleep = orig_sleep

        # ---- server._synth_rest: chirp cache key, gemini atomic cache + metering
        import server
        import speech_text
        txt = "The hidden stalkers tracked his every step."
        key = hashlib.sha1(f"{gemini_tts.CHIRP_VOICE}|{speech_text.speakable(txt)}".encode()).hexdigest()
        cache = server._TTS_CACHE_DIR
        os.makedirs(cache, exist_ok=True)
        cpath = os.path.join(cache, key + ".mp3")
        had = os.path.exists(cpath)
        if not had:
            open(cpath, "wb").write(b"legacy-audio")
        dst = os.path.join(tempfile.mkdtemp(), "o.mp3")
        server._synth_rest(txt, dst, engine=gemini_tts.engine_for_project(old))
        check("Chirp projects reuse the lines already recorded (original cache key)",
              open(dst, "rb").read() == open(cpath, "rb").read())
        if not had:
            os.remove(cpath)

        metered = {}

        @contextlib.contextmanager
        def fake_gate(kind, units, model=""):
            class M:
                def tokens(self, p, o, c=0):
                    metered.update(p=p, o=o)
            metered["model"] = model
            yield M()
        real_gate, real_synth = server.usage.gate, gemini_tts.synth_gemini_tts

        def fake_synth(text, out_path, cfg=None, style=None, voice=None):
            open(out_path, "wb").write(b"gemini-audio")
            return {"seconds": 6.5, "prompt_tokens": 20, "audio_tokens": 162}
        server.usage.gate, gemini_tts.synth_gemini_tts = fake_gate, fake_synth
        try:
            dst2 = os.path.join(tempfile.mkdtemp(), "g.mp3")
            uniq = "A line only this test says %s." % os.getpid()
            server._synth_rest(uniq, dst2, engine=gemini_tts.engine_for_project(new))
        finally:
            server.usage.gate, gemini_tts.synth_gemini_tts = real_gate, real_synth
        ck = hashlib.sha1(("gemini|gemini-3.8-flash-tts|Charon|%s|%s" % (
            gemini_tts.DEFAULT_STYLE, speech_text.speakable(uniq))).encode()).hexdigest()
        gp = os.path.join(cache, ck + ".mp3")
        check("Gemini lines are cached", os.path.exists(gp) and open(gp, "rb").read() == b"gemini-audio")
        check("no temp files are left beside the cache",
              not [f for f in os.listdir(cache) if f.endswith(".tmp")])
        check("the usage gate is told the real audio tokens",
              metered.get("o") == 162 and metered.get("model") == "gemini-3.8-flash-tts")
        if os.path.exists(gp):
            os.remove(gp)

        # ---- pricing
        import usage
        c = usage.gemini_tts_cost("gemini-3.8-flash-tts", 20, 162)
        check("a 6.5 s line costs about $0.0015 at $9/1M audio tokens", abs(c - 0.001468) < 1e-5)
        check("Flash-Lite is priced at $6/1M audio tokens",
              abs(usage.gemini_tts_cost("gemini-3.8-flash-lite-tts", 0, 1_000_000) - 6.0) < 1e-9)
        check("Chirp keeps its per-character rate",
              abs(usage._est_cost("tts", 1000, "chirp3-hd-charon")
                  - usage.EST_COST_PER_TTS_1K_CHARS_USD) < 1e-9)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ---- the operator's choice: per chapter, and a studio default
    os.environ["GEMINI_API_KEY"] = "mock_gemini"; os.environ["TTS_API_KEY"] = "mock_chirp"
    root = tempfile.mkdtemp(prefix="vroot_")
    ch = os.path.join(root, "series_1")
    gemini_tts.save_choice(ch, "gemini:Puck", "calm, clear storyteller, steady pace")
    e = gemini_tts.engine_for_project(ch)
    check("a voice picked at ingest is used for a new chapter",
          e["voice"] == "Puck" and e["style"].startswith("calm"))
    ch2 = os.path.join(root, "series_2")
    gemini_tts.save_default(root, "chirp:Charon")
    check("without a pick, a new chapter gets the saved studio default",
          gemini_tts.engine_for_project(ch2)["provider"] == "chirp")
    ch3 = os.path.join(root, "series_3"); os.makedirs(os.path.join(ch3, "audio"))
    json.dump({"provider": "gemini", "model": "gemini-3.8-flash-tts", "voice": "Charon",
               "style": ""}, open(os.path.join(ch3, "tts.json"), "w"))
    gemini_tts.save_choice(ch3, "gemini:Algenib")
    check("a chapter that already has a voice keeps it (no mixed narrators)",
          gemini_tts.engine_for_project(ch3)["voice"] == "Charon")
    try:
        gemini_tts.parse_choice("gemini:NotAVoice")
        check("an unknown voice is refused", False)
    except ValueError:
        check("an unknown voice is refused", True)
    check("31 voices are offered: classic Chirp Charon + 30 Gemini voices",
          len(gemini_tts.voice_options()) == 31
          and gemini_tts.voice_options()[0]["id"] == "chirp:Charon")

    # ---- the API, against a temporary projects root
    import ingest as _ing
    from fastapi.testclient import TestClient
    saved_root = _ing.PROJECTS
    _ing.PROJECTS = root
    try:
        c = TestClient(server.app)
        v = c.get("/api/voices").json()
        check("/api/voices lists the voices and the current default",
              len(v["voices"]) == 31 and v["default"]["id"] == "chirp:Charon")
        r = c.post("/api/voices/default", json={"voice": "gemini:Algenib", "style": ""})
        check("Make default saves the studio default",
              r.status_code == 200 and c.get("/api/voices").json()["default"]["id"] == "gemini:Algenib")
        check("a bad voice is a 400", c.post("/api/voices/default", json={"voice": "x:y"}).status_code == 400)
        calls = []
        real = server._synth_rest
        server._synth_rest = lambda text, out, style=None, engine=None: (
            calls.append(engine["voice"]), open(out, "wb").write(b"mp3"))
        try:
            p1 = c.post("/api/voices/preview", json={"voice": "gemini:Puck", "style": ""}).json()
            c.post("/api/voices/preview", json={"voice": "gemini:Puck", "style": ""})
        finally:
            server._synth_rest = real
        check("a preview is recorded once, then served from cache",
              calls == ["Puck"] and c.get(p1["url"]).status_code == 200)
        check("preview file names are validated", c.get("/api/voices/preview/..%2Fx.mp3").status_code == 404)
    finally:
        _ing.PROJECTS = saved_root

    src = open(os.path.join(HERE, "ingest.py"), encoding="utf-8").read()
    fresh = src[src.index("if fresh:"):src.index("desc_path = os.path.join(proj")]
    check("a fresh re-ingest drops the voice pin (all lines are re-recorded)",
          '"tts.json"' in fresh)

    for n, ok in R:
        print(("PASS " if ok else "FAIL ") + n)
    n_ok = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n_ok, len(R)))
    return 0 if n_ok == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
