"""Approve applies the current studio voice; exports play at 1.25x (owner,
2026-10-04). Real (silent) MP3s so the timeline measures real lengths; TTS is
faked; temp projects root. No network, no money."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
os.environ.setdefault("GEMINI_API_KEY", "test-not-a-key")   # mock: TTS is faked below
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def mp3(path, secs):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", str(secs), "-q:a", "9", path], check=True)


def main():
    import ingest
    import server
    import gemini_tts
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="av_")
    work = tempfile.mkdtemp(prefix="avw_")
    pid = "series-x_1"
    pdir = os.path.join(root, pid)
    os.makedirs(os.path.join(pdir, "audio"))
    os.makedirs(os.path.join(pdir, "clips"))
    mp3(os.path.join(pdir, "audio", "beat_000.mp3"), 4.0)
    mp3(os.path.join(pdir, "audio", "beat_001.mp3"), 4.0)
    segs = [{"seg_index": 0, "panel_id": "p1", "start": 0.0, "dur": 4.35 + 0.35, "clip": "clips/seg_000.mp4",
             "beats": [{"index": 0, "text": "He drew his sword.", "start": 0.35, "end": 4.35}]},
            {"seg_index": 1, "panel_id": "p2", "start": 4.7, "dur": 12.0, "clip": "clips/seg_001.mp4",
             "beats": [{"index": 1, "text": "She smiled.", "start": 5.05, "end": 9.05}]}]
    json.dump(segs, open(os.path.join(pdir, "segments.json"), "w"))
    json.dump({"id": pid, "url": "https://example.com/x/1", "chapter": "1"}, open(os.path.join(pdir, "project.json"), "w"))
    json.dump({"provider": "gemini", "model": "gemini-3.8-flash-tts", "voice": "Charon",
               "style": "dramatic, engaging manhwa recap narrator"}, open(os.path.join(pdir, "tts.json"), "w"))
    for i in (0, 1):
        open(os.path.join(pdir, "clips", f"seg_{i:03d}.mp4"), "wb").write(b"x")
    saved = (ingest.PROJECTS, server.WORK, server._synth_rest)
    ingest.PROJECTS, server.WORK = root, work
    server._ACTIVE_CACHE["key"] = None
    recorded = []

    def fake_synth(text, out_path, style=None, engine=None):
        recorded.append((text, engine.get("style")))
        mp3(out_path, 2.0)                       # the new voice is faster
    server._synth_rest = fake_synth
    try:
        c = TestClient(server.app)
        check("activate the test chapter", c.post("/api/activate", json={"id": pid}).status_code == 200)
        check("no studio default -> nothing to re-voice", server._voice_outdated(pdir) is None)
        gemini_tts._write_json(os.path.join(root, gemini_tts.DEFAULT_FILE),
                               {"provider": "gemini", "model": "gemini-3.8-flash-tts", "voice": "Charon",
                                "style": "energetic, fast-paced YouTube recap host"})
        check("a new studio style makes the chapter's voice outdated", server._voice_outdated(pdir) is not None)
        j = {}
        server.JOBS["t1"] = j
        n = server._revoice_if_outdated(pdir, j, "t1")
        check("every narrated line is re-recorded in the studio style",
              n == 2 and all(st == "energetic, fast-paced YouTube recap host" for _, st in recorded))
        pin = json.load(open(os.path.join(pdir, "tts.json")))
        check("the chapter is now pinned to the studio voice", pin.get("style") == "energetic, fast-paced YouTube recap host")
        s2 = json.load(open(os.path.join(pdir, "segments.json")))
        by = {s["seg_index"]: s for s in s2}
        check("an automatic segment re-fits the shorter audio (no dead air)", by[0]["dur"] < 3.5)
        check("a segment you lengthened by hand keeps its length", abs(by[1]["dur"] - 12.0) < 1e-6)
        check("downstream segments shift to follow", by[1]["start"] < 4.7)
        check("old clips are marked stale so they re-render",
              not os.path.exists(os.path.join(pdir, "clips", "seg_000.mp4")))
        check("approving again with the same voice re-voices nothing",
              server._revoice_if_outdated(pdir, {}, "t1") == 0)

        # Owner, 2026-10-05 (ch.30): a re-voice that can't finish must leave the
        # chapter exactly as it was — pin, timeline and audio.
        gemini_tts._write_json(os.path.join(root, gemini_tts.DEFAULT_FILE),
                               {"provider": "gemini", "model": "gemini-3.8-flash-tts", "voice": "Charon", "style": ""})
        plan = server._revoice_plan(pdir)
        check("the render sheet gets the re-voice plan (lines, cost, budget)",
              plan and plan["lines"] == 2 and plan["est_usd"] >= 0 and "no style" in plan["to"]
              and "energetic" in plan["from"])
        before = {f: open(os.path.join(pdir, f), "rb").read() for f in ("tts.json", "segments.json", "audio/beat_000.mp3")}
        same = lambda: all(open(os.path.join(pdir, f), "rb").read() == v for f, v in before.items())
        real_summary = server.usage.daily_summary
        server.usage.daily_summary = lambda: {"est_cost_usd": server.usage.MAX_DAILY_SPEND_USD}
        recorded.clear()
        try:
            server._revoice_if_outdated(pdir, {}, "t1")
            check("over budget -> refused", False)
        except RuntimeError as e:
            check("over budget -> refused up front with a plain reason",
                  "Nothing was changed" in str(e) and "chapter's own voice" in str(e))
        finally:
            server.usage.daily_summary = real_summary
        check("over budget -> nothing recorded, chapter untouched", not recorded and same())
        calls = []

        def failing_synth(text, out_path, style=None, engine=None):
            calls.append(text)
            if len(calls) == 2:
                raise server.usage.UsageCapExceeded("MAX_DAILY_SPEND_USD would be exceeded")
            mp3(out_path, 2.0)
        server._synth_rest = failing_synth
        try:
            server._revoice_if_outdated(pdir, {}, "t1")
            check("cap hit mid-way -> error", False)
        except server.usage.UsageCapExceeded:
            pass
        check("cap hit mid-way -> pin, timeline and audio unchanged; no half-voiced chapter",
              same() and not os.path.exists(os.path.join(pdir, "audio", ".revoice")))
        check("the chapter still reads as needing the new voice", server._voice_outdated(pdir) is not None)
        server._synth_rest = fake_synth
    finally:
        ingest.PROJECTS, server.WORK, server._synth_rest = saved
        server._ACTIVE_CACHE["key"] = None
        server.JOBS.pop("t1", None)

    import studio_settings
    check("exports default to 1.25x (studio setting)", studio_settings.DEFAULTS["export_speed"] == 1.25)
    src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    check("approve-and-render exports at EXPORT_SPEED and applies the voice first",
          "speed = _studio.export_speed()" in src and "res = _do_export(speed)" in src
          and "_revoice_if_outdated(pdir, j, job_id)" in src)
    check("render in the chapter's own voice skips the re-voice",
          'nv = 0 if j.get("keep_voice") else _revoice_if_outdated(pdir, j, job_id)' in src
          and "keep_voice: bool = False" in src)
    check("saving the studio voice is logged in Activity", 'f"Studio voice set to {_voice_label(rec)}' in src)
    check("only the sped-up file is kept", "os.remove(out)        # keep only the version that will be posted" in src)
    check("Projects flags an approved chapter whose video was deleted",
          'it["video_missing"] = st == "approved" and not n_exports' in src)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
