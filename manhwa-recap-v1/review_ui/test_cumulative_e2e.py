"""Cumulative releases, end to end through the SERVER build path with real
ffmpeg on generated clips (owner, 2026-11-13/14): an approved/posted
compilation 1-4 plus a new chapter 5 gives 1-5 — the old video re-used as it
is, only the internal "To be continued" card removed, nothing generated again.

Each generated chapter has its own tone (300 + 100·n Hz) and a red closing
card with a 2 kHz tone, so order, missing/duplicated pieces and leftover cards
can be heard in the joined file. No network, no paid call."""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import ingest  # noqa: E402
import media_qa  # noqa: E402
import range_compile as rc  # noqa: E402
import release_gate as rg  # noqa: E402
import server  # noqa: E402
import usage  # noqa: E402
import yt_api  # noqa: E402
from gate_fixtures import CLEAN, OPEN, w, write_qa  # noqa: E402

pytestmark = pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0,
                                reason="needs ffmpeg")
CARD_HZ, OUTRO = 2000, 2.0


def _av(path, secs, video, hz):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{video}{':' if '=' in video else '='}s=320x180:r=30:d={secs}",
                    "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={secs}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(path)], check=True)


def tone(path, at, span=0.4):
    """Dominant frequency (Hz) of the soundtrack around `at` seconds."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, at):.3f}", "-t", str(span), "-i", str(path),
                          "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], check=True, capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(float)
    if len(x) < 800:
        return 0
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return int(round(np.fft.rfftfreq(len(x), 1 / 8000)[int(spec.argmax())] / 50.0) * 50)


def decode_errors(path):
    out = ""
    for sel in (["-map", "0:v:0", "-an"], ["-map", "0:a:0", "-vn"]):
        out += subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path), *sel, "-f", "null", "-"],
                              capture_output=True, text=True).stderr
    return out


def stream_seconds(path, kind):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"{kind}:0", "-show_entries", "stream=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip().split("\n")[0])


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Lib:
    def __init__(self, root, tmp):
        self.root, self.tmp, self.story = root, tmp, {}

    def chapter(self, n, secs, critique=CLEAN, review="review_pending"):
        """A finished chapter export the studio's way: story + closing card,
        with its render manifest, QA report and bound script check."""
        from media_concat import concatenate
        pid = f"saga_{n}"
        d = os.path.join(self.root, pid)
        os.makedirs(os.path.join(d, "exports", "_qa"), exist_ok=True)
        story, card = self.tmp / f"story{n}.mp4", self.tmp / f"card{n}.mp4"
        _av(story, secs, "testsrc2", 300 + 100 * n)
        _av(card, OUTRO, "color=c=red", CARD_HZ)
        name = "final.mp4"
        concatenate([story, card], os.path.join(d, "exports", name))
        self.story[n] = rc._video_seconds(str(story))
        lines = [f"Chapter {n}, line {i}." for i in range(3)]
        beats = []
        for i in range(3):                               # the voice files final-media QA measures
            wav = os.path.join(d, "audio", f"beat_{i}.wav")
            os.makedirs(os.path.dirname(wav), exist_ok=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + 100 * n}:duration=0.9",
                            "-ar", "48000", "-ac", "1", wav], check=True)
            beats.append(wav)
        w(os.path.join(d, "project.json"), {"id": pid, "series": "Saga", "series_id": "saga", "chapter": str(n)})
        w(os.path.join(d, "exports", "_qa", name + ".manifest.json"), {
            "version": 1, "context": {"kind": "chapter", "speed": 1.0, "cards": [{"role": "outro", "path": str(card)}]},
            "events": [{"beat": i, "text": t, "start": float(i), "end": i + 0.9, "audio": beats[i], "source_project": d,
                        "speed": 1.0} for i, t in enumerate(lines)],
            "expected_duration": self.story[n] + OUTRO, "capture_errors": [],
            "input_digest": f"inputs-{n}", "export_digest": sha(os.path.join(d, "exports", name))})
        write_qa(d, name)
        w(os.path.join(d, "critique.json"), {**critique, "script_sha256": "s", "narration_sha256": rg.narration_digest(lines)})
        w(os.path.join(d, "reviews.json"), {name: {"status": review}})
        return d

    def post(self, pid, name):
        d = os.path.join(self.root, pid)
        w(os.path.join(d, "publishes.json"), {name: {"status": "published", "results": [
            {"status": "published", "network": "youtube", "url": "https://www.youtube.com/watch?v=abcdefghijk",
             "published_at": time.time()}]}})
        server.save_publish(d, {name: {"title": "[1-4] Saga RISES | Flamingo Recap", "description": "Recap.",
                                       "tags": ["saga chapter 1-4", "manhwa recap"]}}, actor="owner")

    def wait(self, pid):
        for _ in range(600):
            m = server._read_json(os.path.join(self.root, pid, "project.json")) or {}
            if (m.get("build") or {}).get("status") in ("done", "error") and pid not in server._RANGE_BUILDING:
                return m
            time.sleep(0.05)
        raise AssertionError(f"{pid} never finished")

    def plan(self):
        return next(x for x in server._auto_append_pass() if x["series"] == "saga")


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = str(tmp_path / "projects")
    os.makedirs(root)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "_yt_root", lambda: os.path.join(root, "_yt"))
    monkeypatch.setattr(server, "_prepare_publish", lambda *a, **k: None)
    monkeypatch.setattr(server, "api_thumbcopilot_generate", lambda *a, **k: None)
    monkeypatch.setattr(server, "_drive_copy_later", lambda *a, **k: None)
    monkeypatch.setattr(server._drive, "record", lambda p: None)
    monkeypatch.setattr(yt_api, "configured", lambda: False)
    # final-media QA runs for real, in line (the app runs it on a thread)
    monkeypatch.setattr(server._mqa, "start", lambda pdir, name: media_qa.inspect(pdir, name))
    usage_dir = tmp_path / "usage"
    usage_dir.mkdir()
    monkeypatch.setattr(usage, "USAGE_DIR", str(usage_dir))
    monkeypatch.setattr(usage, "LOG_PATH", str(usage_dir / "calls.log.jsonl"))
    monkeypatch.setattr(usage, "COUNTS_PATH", str(usage_dir / "counters.json"))
    monkeypatch.setattr(usage, "LOCK_PATH", str(usage_dir / ".lock"))
    for k in ("AUTO_APPEND_MODE", "AUTO_APPEND_MIN", "AUTO_APPEND_HOLD", "STITCH_ENGINE", "RELEASE_GATE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AUTO_APPEND", "1")
    L = Lib(root, tmp_path)
    for n, secs in ((1, 4.0), (2, 5.0), (3, 4.5), (4, 6.0)):
        L.chapter(n, secs)
    r = server.api_range_build(server.RangeIn(projects=[f"saga_{n}" for n in (1, 2, 3, 4)]))
    assert r["project"] == "saga_ch1-4"
    assert L.wait("saga_ch1-4")["build"]["status"] == "done"
    L.post("saga_ch1-4", "final_range_1-4.mp4")
    return L


def _file(L, pid, name):
    return os.path.join(L.root, pid, "exports", name)


def test_posted_1_to_4_plus_new_chapter_5_gives_1_to_5(lib):
    L = lib
    base = _file(L, "saga_ch1-4", "final_range_1-4.mp4")
    base_secs, base_sha = rc._probe(base), sha(base)
    assert abs(base_secs - (sum(L.story[n] for n in (1, 2, 3, 4)) + OUTRO)) < 0.15   # three inner cards already gone
    L.chapter(5, 5.5)
    ch5 = _file(L, "saga_5", "final.mp4")
    ch5_secs, ch5_sha = rc._probe(ch5), sha(ch5)

    d = L.plan()
    assert d["action"] == "build" and (d["start"], d["end"]) == (1, 5) and d["projects"] == ["saga_ch1-4", "saga_5"]
    m = L.wait("saga_ch1-5")
    assert m["build"]["status"] == "done", m
    out = _file(L, "saga_ch1-5", "final_range_1-5.mp4")

    # re-used, not regenerated: the pieces are byte-identical and no paid call was made
    assert sha(base) == base_sha and sha(ch5) == ch5_sha
    assert not os.path.exists(usage.LOG_PATH) and not os.path.exists(usage.COUNTS_PATH)
    assert [x["project"] for x in m["merged_from"]] == ["saga_ch1-4", "saga_5"]

    # length: old video + new chapter − the old video's closing card (now internal)
    total = rc._probe(out)
    assert abs(total - (base_secs + ch5_secs - OUTRO)) < 0.15, (total, base_secs, ch5_secs)
    assert abs(total - (sum(L.story.values()) + OUTRO)) < 0.2

    # chapter markers: one per chapter, in order, each where that chapter's story starts
    marks = [(int(n), float(t)) for n, t in m["build"]["marks"]]
    assert [n for n, _t in marks] == [1, 2, 3, 4, 5]
    starts = [sum(L.story[k] for k in range(1, n)) for n in range(1, 6)]
    for (n, t), want in zip(marks, starts):
        assert abs(t - want) < 0.1, (n, t, want)

    # heard: every chapter present once, in order; no card left inside; the last card kept
    for (n, t), nxt in zip(marks, [x[1] for x in marks[1:]] + [total - OUTRO]):
        assert tone(out, t + 0.5) == 300 + 100 * n, f"chapter {n} is not at its marker"
        assert tone(out, nxt - 0.6) == 300 + 100 * n, f"chapter {n} does not run to the next marker"
    for _n, t in marks[1:]:
        assert tone(out, t - 0.45, 0.4) != CARD_HZ and tone(out, t + 0.05, 0.4) != CARD_HZ, "a closing card was left inside"
    assert tone(out, total - 1.0) == CARD_HZ

    # timestamps and sound: decodes without error, sound as long as the picture
    assert decode_errors(out) == ""
    assert abs(stream_seconds(out, "v") - stream_seconds(out, "a")) < 0.1

    # packaging carried over from the posted video, relabelled for the new range
    md = server.load_publish(os.path.join(L.root, "saga_ch1-5"))["final_range_1-5.mp4"]
    assert md["title"] == "[1-5] Saga RISES | Flamingo Recap"
    assert "saga chapter 1-5" in [x.lower() for x in md["tags"]]
    for n in (1, 2, 3, 4, 5):
        assert f"Chapter {n}" in md["description"]
    assert m["chapters_start"] == 1 and m["chapters_end"] == 5

    # evidence: every piece is recorded with the hash of the bytes that went in
    ev = json.load(open(os.path.join(L.root, "saga_ch1-5", rg.EVIDENCE)))["exports"]["final_range_1-5.mp4"]["components"]
    assert [(e["project"], e["export_sha256"]) for e in ev] == [("saga_ch1-4", base_sha), ("saga_5", ch5_sha)]
    assert [c["project"] for c in ev[0]["components"]] == ["saga_1", "saga_2", "saga_3", "saga_4"]
    v = rg.verdict(os.path.join(L.root, "saga_ch1-5"), "final_range_1-5.mp4")
    assert v["critical"] == [] and not [x for x in v["unknown"] if x["code"].startswith(("component_", "evidence_"))], v

    # the new chapter is not left to post on its own, and is not rendered again
    assert server.load_reviews(os.path.join(L.root, "saga_5"))["final.mp4"]["merged_into"] == "saga_ch1-5"
    assert L.plan()["action"] == "none"


def test_a_defect_in_one_chapter_follows_it_into_every_later_compilation(lib):
    L = lib
    L.chapter(5, 3.0, critique=OPEN)
    L.plan()
    L.wait("saga_ch1-5")
    v = rg.verdict(os.path.join(L.root, "saga_ch1-5"), "final_range_1-5.mp4")
    assert v["release"] == "blocked" and [(x["code"], x["chapter"]) for x in v["critical"]] == [("script_unresolved", "saga_5")]


def test_chain_through_cleanup_1_4_then_5_then_6(lib):
    """Each release extends the last; the earlier chapter folders are cleaned
    up on the way and the closing card is still found and removed."""
    import shutil
    L = lib
    L.chapter(5, 3.0)
    L.plan()
    L.wait("saga_ch1-5")
    L.post("saga_ch1-5", "final_range_1-5.mp4")
    for n in (1, 2, 3, 4, 5):
        shutil.rmtree(os.path.join(L.root, f"saga_{n}"))         # chapters cleaned up after posting
    shutil.rmtree(os.path.join(L.root, "saga_ch1-4"))
    L.chapter(6, 3.5)
    d = L.plan()
    assert d["action"] == "build" and (d["start"], d["end"]) == (1, 6) and d["projects"] == ["saga_ch1-5", "saga_6"]
    m = L.wait("saga_ch1-6")
    assert m["build"]["status"] == "done", m
    out = _file(L, "saga_ch1-6", "final_range_1-6.mp4")
    total = rc._probe(out)
    assert abs(total - (sum(L.story.values()) + OUTRO)) < 0.25
    marks = [(int(n), float(t)) for n, t in m["build"]["marks"]]
    assert [n for n, _t in marks] == [1, 2, 3, 4, 5, 6]
    for n, t in marks:
        assert tone(out, t + 0.5) == 300 + 100 * n
    assert tone(out, marks[5][1] - 0.45) != CARD_HZ and tone(out, total - 1.0) == CARD_HZ
    assert decode_errors(out) == ""
    # evidence for chapters whose folders are gone came along inside the compilation
    v = rg.verdict(os.path.join(L.root, "saga_ch1-6"), "final_range_1-6.mp4")
    assert v["critical"] == [] and "component_missing" not in [x["code"] for x in v["unknown"]], v
    # final-media QA of the compilation passed on its own file; the voice files of the
    # cleaned-up chapters are reported as not re-measured, not as broken narration
    q = json.load(open(os.path.join(L.root, "saga_ch1-6", "exports", "_qa", "final_range_1-6.mp4.qa.json")))
    assert q["status"] == "passed" and q["metrics"]["source_voice_unavailable"] == 15
    assert "15 of 18 lines not re-measured" in q["checks"]["source_voice"]


def test_chapters_that_finish_out_of_order_wait_for_the_gap(lib):
    L = lib
    L.chapter(6, 3.0)                                  # 6 is ready before 5
    d = L.plan()
    assert d["action"] == "none" and not os.path.isdir(os.path.join(L.root, "saga_ch1-6"))
    L.chapter(5, 3.0)
    d = L.plan()
    assert d["action"] == "build" and (d["start"], d["end"]) == (1, 6)
    m = L.wait("saga_ch1-6")
    assert [int(n) for n, _t in m["build"]["marks"]] == [1, 2, 3, 4, 5, 6]
    assert [x["project"] for x in m["merged_from"]] == ["saga_ch1-4", "saga_5", "saga_6"]


def test_two_requests_for_the_same_release_build_it_once(lib, monkeypatch):
    L = lib
    L.chapter(5, 3.0)
    gate, calls = threading.Event(), []
    real = rc.stitch

    def slow(chs, out, **k):
        calls.append(out)
        gate.wait(10)
        return real(chs, out, **k)
    monkeypatch.setattr(rc, "stitch", slow)
    body = lambda: server.RangeIn(projects=["saga_ch1-4", "saga_5"])
    results = []

    def go():
        try:
            results.append(server.api_range_build(body())["project"])
        except server.HTTPException as e:
            results.append(e.status_code)
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(map(str, results)) == ["409", "409", "409", "saga_ch1-5"]
    gate.set()
    assert L.wait("saga_ch1-5")["build"]["status"] == "done" and len(calls) == 1
    assert "saga_ch1-5" not in server._RANGE_BUILDING           # the slot is free again


def test_an_approved_or_queued_release_is_not_rebuilt_over(lib):
    L = lib
    L.chapter(5, 3.0)
    L.plan()
    L.wait("saga_ch1-5")
    d = os.path.join(L.root, "saga_ch1-5")
    before = sha(_file(L, "saga_ch1-5", "final_range_1-5.mp4"))
    w(os.path.join(d, "reviews.json"), {"final_range_1-5.mp4": {"status": "approved"}})
    with pytest.raises(server.HTTPException) as e:                # a manual rebuild of the same range
        server.api_range_build(server.RangeIn(projects=["saga_ch1-4", "saga_5"]))
    assert e.value.status_code == 409 and "approved" in e.value.detail
    assert sha(_file(L, "saga_ch1-5", "final_range_1-5.mp4")) == before
    # chapter 6 arrives: the approved 1-5 is not replaced; 6 waits for it to post
    L.chapter(6, 3.0)
    p = L.plan()
    assert p["action"] == "wait" and "approved" in p["why"] and not os.path.isdir(os.path.join(L.root, "saga_ch1-6"))
    assert sha(_file(L, "saga_ch1-5", "final_range_1-5.mp4")) == before


def test_posted_chapter_guard_stops_re_rendering_but_not_reuse(lib):
    L = lib
    cover = server._posted_cover(os.path.join(L.root, "saga_3"))
    assert cover and cover["project"] == "saga_ch1-4"            # chapter 3 is inside a posted video: not rendered again
    L.chapter(5, 3.0)
    assert L.plan()["action"] == "build"                         # …and that posted video is still re-used
    assert L.wait("saga_ch1-5")["build"]["status"] == "done"
