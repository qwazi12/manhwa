"""Owner, 2026-11-18: "can't you handle everything so the only thing we need
to pay for is voice?" A chapter written by hand: the studio cuts the panels,
the writer hands over what each panel shows and the spoken lines, and the
studio records the voice. Every other paid step is skipped and, as a second
lock, refused by the spend gate.
No network: every service boundary is faked (test_generation_cache's harness).
Run: python3 -m pytest test_hand_made.py -q"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
from test_generation_cache import cache, ingest_fixture, offline  # noqa: E402,F401
import hand_made  # noqa: E402
import usage  # noqa: E402

URL = "https://asuracomic.net/series/offline-story-12345678/chapter/1"
PANELS = ["page001_panel_001", "page001_panel_002", "page001_panel_003"]
LINES = [("page001_panel_001", "A boy waits alone in a blue room.", 1),
         ("page001_panel_001", "Nobody has come for him in three days.", 1),
         ("page001_panel_003", "Then the door opens, and a soldier steps inside.", 1),
         ("page001_panel_002", "Far away, a red tower burns against the night sky.", 2),
         ("page001_panel_003", "The soldier tells him it is time to leave.", 3)]


def _art(path, seed):
    """A picture with plenty of ink, so the free image check calls it art."""
    im = Image.effect_noise((300, 420), 60).convert("RGB")
    im.paste((seed * 40 % 255, 90, 160), (20, 20, 120, 160))
    im.save(path)


def _body(**over):
    b = {"by": "test", "panels": [{"panel_id": p, "visual_description": f"A boy stands in a room, picture {i}.",
                                   "ocr_text": "Come back." if i == 0 else ""} for i, p in enumerate(PANELS)],
         "lines": [{"panel": p, "text": t, "scene": s} for p, t, s in LINES]}
    b.update(over)
    return b


@pytest.fixture
def studio(ingest_fixture, monkeypatch):
    """A chapter that has been prepared (3 panels), with counters on every paid boundary."""
    import ingest
    pdir, counts, run, reached = ingest_fixture
    counts.update(download=0, split=0, describe=0, vision_split=None)

    def download(url, pages):
        counts["download"] += 1
        _art(Path(pages) / "page001.png", 9)
        return [str(Path(pages) / "page001.png")]

    def split(pages, crops, **kw):
        counts["split"] += 1
        counts["vision_split"] = kw.get("vision")
        for i, p in enumerate(PANELS):
            _art(Path(crops) / f"{p}.png", i)
        return len(PANELS), {"format": "strip", "pages": 1, "panels": len(PANELS)}

    def describe_subprocess(cmd=(), *a, **kw):
        # subprocess.run is shared: only the describe program counts as "described"
        if any("panel-describe" in str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd])):
            counts["describe"] += 1
        return SimpleNamespace(returncode=0, stdout="1.0", stderr="")

    monkeypatch.setitem(sys.modules, "scraper", SimpleNamespace(download_chapter=download, LAST_WARNING=""))
    monkeypatch.setitem(sys.modules, "splitlab", SimpleNamespace(split_into=split))
    monkeypatch.setattr(ingest.subprocess, "run", describe_subprocess)
    monkeypatch.setattr(usage, "PROJECTS_DIR", str(pdir.parent))
    usage._VOICE_ONLY_MEMO.clear()
    import matcher
    import panel_stats
    monkeypatch.setattr(matcher, "match_beats_to_panels",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("the paid matcher ran")))
    monkeypatch.setattr(panel_stats, "annotate", lambda *a: {"art": len(PANELS)})
    prep = hand_made.prepare(URL, str(pdir), slug=pdir.name)
    hand_made.prep_save(str(pdir), {"url": URL, "variant": "", "state": "ready", "at": 1.0, **prep})
    return pdir, counts, ingest


def _write(pdir, **over):
    rec, problems = hand_made.validate(str(pdir), _body(**over))
    assert problems == [], problems
    hand_made.save(str(pdir), rec)
    return rec


def test_preparing_downloads_and_cuts_without_the_paid_check(studio):
    pdir, counts, ingest = studio
    assert (counts["download"], counts["split"], counts["vision_split"]) == (1, 1, False)
    assert [p["panel_id"] for p in hand_made.panel_listing(str(pdir))] == PANELS
    assert all(p["sha256"] and p["width"] == 300 for p in hand_made.panel_listing(str(pdir)))
    assert not list(pdir.glob(".source-staging-*"))


def test_the_real_cutter_asks_no_model_when_told_not_to(monkeypatch, tmp_path):
    sys.modules.pop("splitlab", None)
    import splitlab
    monkeypatch.setattr(splitlab, "_default_ask",
                        lambda: (_ for _ in ()).throw(AssertionError("the paid check was set up")))
    Image.effect_noise((400, 900), 50).convert("RGB").save(tmp_path / "page001.png")
    (tmp_path / "crops").mkdir()
    n, stats = splitlab.split_into([str(tmp_path / "page001.png")], str(tmp_path / "crops"), vision=False)
    assert n >= 1 and stats["panels"] == n


@pytest.mark.parametrize("change, said", [
    (lambda b: b["panels"].pop(), "1 panel(s) have no description: page001_panel_003"),
    (lambda b: b["panels"].append({"panel_id": "page009_panel_001", "visual_description": "x"}), "no panel 'page009_panel_001'"),
    (lambda b: b["panels"].append(dict(b["panels"][0])), "described twice"),
    (lambda b: b["panels"][0].update(visual_description=""), "no visual_description"),
    (lambda b: b["panels"][0].update(subject_box=[0.2, 0.2, 0.21, 0.9]), "subject_box must be"),
    (lambda b: b["panels"][0].update(turn_cw=45), "turn_cw must be"),
    (lambda b: b["lines"][0].update(panel="page001_panel_099"), "lines[0]: no panel"),
    (lambda b: b["lines"][1].update(text=" "), "lines[1]: no text"),
    (lambda b: b["lines"][2].update(text="word " * 46), "lines[2]: 46 words"),
    (lambda b: b["lines"][3].update(text="I cannot write a line-by-line script for this work, but I can provide a summary."),
     "reads like a note from the writer"),
    (lambda b: b.update(lines=b["lines"][:4]), "a chapter needs at least 5"),
    (lambda b: b.update(packaging=["x"]), "packaging must be an object"),
])
def test_a_hand_over_with_a_fault_is_refused_and_nothing_is_saved(studio, change, said):
    pdir, counts, ingest = studio
    body = _body()
    change(body)
    rec, problems = hand_made.validate(str(pdir), body)
    assert rec is None and any(said in p for p in problems), problems
    assert not hand_made.is_hand(str(pdir))


def test_the_chapter_is_built_from_the_hand_over_and_only_the_voice_is_recorded(studio):
    pdir, counts, ingest = studio
    _write(pdir)
    said = []
    meta = ingest.run_ingest(URL, lambda *a: said.append(a), direct=False)
    # nothing downloaded or cut again, nothing described, no script model, no matcher
    assert (counts["download"], counts["split"], counts["describe"], counts["narrate"]) == (1, 1, 0, 0)
    assert counts["voice"] == len(LINES)
    assert meta["made_by"] == "hand" and meta["match_method"] == "by hand" and meta["n_segments"] >= 4
    desc = json.load(open(pdir / "descriptions.json"))
    assert [d["panel_id"] for d in desc] == PANELS and all(d["source"] == "hand" and d["ok"] for d in desc)
    assert desc[0]["ocr_text"] == "Come back." and desc[0]["generation"]["inputs"]["source_sha256"]
    assert (pdir / "script.txt").read_text().splitlines() == [t for _, t, _ in LINES]
    segs = json.load(open(pdir / "segments.json"))
    shown = [(s["panel_id"], b["text"]) for s in segs for b in s["beats"]]
    assert shown == [(p, t) for p, t, _ in LINES]          # every line on the panel it names, in the order given
    assert any(a[0] == "describe" and "by hand" in a[1] for a in said)
    assert cache.described_source(pdir) == {"restored": None}
    # a second run (a step re-run, a resume) pays nothing more
    ingest.run_ingest(URL, lambda *a: None, direct=False)
    assert counts["voice"] == len(LINES) and counts["narrate"] == 0


def test_the_spend_gate_refuses_everything_but_voice_for_a_hand_chapter(studio, monkeypatch, tmp_path):
    pdir, counts, ingest = studio
    monkeypatch.setattr(usage, "USAGE_DIR", str(tmp_path / "u"))
    for name in ("LOG_PATH", "COUNTS_PATH", "LOCK_PATH"):
        monkeypatch.setattr(usage, name, str(tmp_path / "u" / os.path.basename(getattr(usage, name))))
    os.makedirs(tmp_path / "u")
    pid = pdir.name
    with usage.scope(job_id="t", stage="framing", project=pid):
        with usage.gate("gemini", 1, model="gemini-3.8-flash") as m:      # not written by hand yet: allowed
            m.not_sent()
    _write(pdir)
    assert usage.voice_only(pid) is True and usage.voice_only("some-other-chapter_1") is False
    with usage.scope(job_id="t", stage="framing", project=pid):
        for kind in ("gemini", "claude"):
            with pytest.raises(usage.VoiceOnly) as e:
                with usage.gate(kind, 1, model="m"):
                    raise AssertionError("the paid call was made")
            assert "voice only" in str(e.value) and "framing" in str(e.value)
        assert not isinstance(e.value, usage.UsageCapExceeded)             # the job is not paused for the night
        with usage.gate("tts", 40, model="tts") as m:                       # the voice is still recorded
            m.not_sent()
    assert hand_made.remove(str(pdir)) is True
    assert usage.voice_only(pid) is False and list((pdir / "_revisions").glob("hand-*-removed.json"))


def test_panels_that_changed_after_the_script_was_written_stop_the_chapter(studio):
    pdir, counts, ingest = studio
    _write(pdir)
    _art(pdir / "crops" / "page001_panel_002.png", 77)
    with pytest.raises(RuntimeError) as e:
        ingest.run_ingest(URL, lambda *a: None, direct=False)
    assert "panels changed after the script was written" in str(e.value) and counts["voice"] == 0


def test_a_hand_over_that_cannot_be_read_never_falls_back_to_the_paid_steps(studio):
    pdir, counts, ingest = studio
    (pdir / "hand.json").write_text("{not json")
    with pytest.raises(hand_made.HandError):
        ingest.run_ingest(URL, lambda *a: None, direct=False)
    assert (counts["describe"], counts["narrate"], counts["download"]) == (0, 0, 1)
    assert usage.voice_only(pdir.name) is True


def test_subject_boxes_are_used_by_the_framing_rules_without_a_question(studio, monkeypatch):
    pdir, counts, ingest = studio
    import auto_framing
    import shot_planner
    monkeypatch.setattr(auto_framing, "default_ask",
                        lambda: (_ for _ in ()).throw(AssertionError("a framing question was set up")))
    monkeypatch.setattr(auto_framing, "mode", lambda: "suggest")
    monkeypatch.setattr(shot_planner, "_query_gemini_for_crops",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("a close-up question was asked")))
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.present")
    body = _body()
    body["panels"][1]["subject_box"] = [0.1, 0.1, 0.9, 0.45]
    rec, problems = hand_made.validate(str(pdir), body)
    assert not problems
    hand_made.save(str(pdir), rec)
    ingest.run_ingest(URL, lambda *a: None, direct=False)
    segs = {s["panel_id"]: s for s in json.load(open(pdir / "segments.json"))}
    framed = segs["page001_panel_002"]
    assert framed["focus_source"] == "auto_subject" and framed["crop_bbox_norm"][3] < 0.7
    assert segs["page001_panel_001"]["focus_source"] in ("fallback_full", "multi_beat_punchin")
    assert shot_planner.LAST_AUTO_FRAMING.get("framed") == 1


def test_the_packaging_answer_and_the_tease_come_from_the_hand_over(studio):
    pdir, counts, ingest = studio
    assert hand_made.seo_answer(str(pdir)) is None
    pk = {"titles": [{"text": "He Was LEFT Behind", "why": "the hook", "recommended": True}],
          "summary": "A boy is found.", "description": "A boy waits in a room until a soldier arrives.",
          "tags": ["manhwa recap"], "hashtags": ["#manhwa"],
          "tease": "Three days alone, and then the door opens.", "thumbnail_text": "the door finally opens now",
          "dramatic_beat": "The soldier arrives.", "new_entities": ["the soldier"]}
    _write(pdir, packaging=pk)
    ans = json.loads(hand_made.seo_answer(str(pdir)))
    assert ans["titles"][0]["recommended"] is True and ans["description"].startswith("A boy waits") and "tease" not in ans
    ingest.run_ingest(URL, lambda *a: None, direct=False)
    hb = json.load(open(pdir / "seo_chapter.json"))["hook_beat"]
    import hashlib
    assert hb["script_hash"] == hashlib.sha1((pdir / "script.txt").read_bytes()).hexdigest()[:16]
    assert hb["tease"] == pk["tease"] and hb["thumbnail_text"] == "THE DOOR FINALLY OPENS"   # at most 4 words


def test_the_routes(studio, monkeypatch):
    pdir, counts, ingest = studio
    sys.modules.pop("server", None)            # the harness's stand-in; the real module is wanted here
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SHARED_SECRET", "s")
    queued = []
    monkeypatch.setattr(server, "_enqueue_ingest", lambda url, **kw: queued.append((url, kw)) or "job123")
    monkeypatch.setattr(server, "_ev", lambda *a, **k: None)
    monkeypatch.setattr(server, "_already_made", lambda url: None)
    monkeypatch.setattr(server.threading, "Thread",
                        lambda target, args=(), kwargs=None, daemon=None: SimpleNamespace(start=lambda: target(*args, **(kwargs or {}))))
    c = TestClient(server.app)
    h = {"x-shared-secret": "s"}
    pid = pdir.name
    assert c.get(f"/api/hand/{pid}").status_code == 401
    assert c.post("/api/hand/prepare", headers=h, json={"url": "not a link"}).status_code == 400
    # a version beside the chapter, prepared from scratch
    r = c.post("/api/hand/prepare", headers=h, json={"url": URL, "variant": "hand"})
    assert r.status_code == 200 and r.json()["chapter"] == pid + "-hand"
    st = c.get(f"/api/hand/{pid}-hand", headers=h).json()
    assert st["state"] == "ready" and [p["panel_id"] for p in st["panels"]] == PANELS and "guide" in st
    assert st["prepared"]["n_panels"] == 3 and st["voice_only"] is False and st["written"] is None
    assert c.get(st["panels"][0]["picture"], headers=h).headers["content-type"] == "image/png"
    assert c.get(f"/api/hand/{pid}-hand/panel/nope", headers=h).status_code == 404
    # a faulty script is refused with every problem named; nothing is queued
    bad = _body()
    bad["panels"].pop()
    r = c.post(f"/api/hand/{pid}-hand", headers=h, json=bad)
    assert r.status_code == 422 and "no description" in r.json()["problems"][0] and queued == []
    r = c.post(f"/api/hand/{pid}-hand", headers=h, json=_body())
    assert r.status_code == 200 and r.json() == {"ok": True, "job": "job123", "chapter": pid + "-hand",
                                                 "lines": 5, "replaced": False}
    assert queued == [(URL, {"fresh": False, "variant": "hand", "source": "hand"})]
    st = c.get(f"/api/hand/{pid}-hand", headers=h).json()
    assert st["state"] == "written" and st["voice_only"] is True and st["written"]["lines"] == 5
    # sent again once the chapter has a script: the old script and voice are put away first
    (pdir.parent / (pid + "-hand") / "script.txt").write_text("old")
    assert c.post(f"/api/hand/{pid}-hand", headers=h, json=_body()).json()["replaced"] is True
    assert queued[-1][1]["fresh"] is True
    assert len(list((pdir.parent / (pid + "-hand") / "_revisions").glob("hand-*.json"))) == 1
    # a chapter that already has a board is not prepared over
    (pdir / "segments.json").write_text("[]")
    assert c.post("/api/hand/prepare", headers=h, json={"url": URL}).status_code == 409
    # giving it back to the paid pipeline
    assert c.request("DELETE", f"/api/hand/{pid}-hand", headers=h).json() == {"ok": True, "removed": True}
    assert c.get(f"/api/hand/{pid}-hand", headers=h).json()["voice_only"] is False
    assert c.get("/api/hand/..%2Fx", headers=h).status_code in (400, 404)
