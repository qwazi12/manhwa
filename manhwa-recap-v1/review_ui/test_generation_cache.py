"""M3 cache regressions. Temporary synthetic evidence; no external requests."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

RECAP = Path(__file__).resolve().parent.parent
ROOT = RECAP.parent
sys.path.insert(0, str(RECAP))
sys.path.insert(0, str(ROOT / "panel-describe"))
import generation_cache as cache
import describe

spec = importlib.util.spec_from_file_location("describe_runner", ROOT / "panel-describe/run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*a, **k):
        raise AssertionError("Network forbidden in cache tests")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setenv("MIRROR_DISCOVERY", "0")
    monkeypatch.setenv("AUTOPILOT_SCHEDULER", "0")


def json_at(path):
    return json.loads(Path(path).read_text())


@pytest.fixture
def description_run(tmp_path, monkeypatch):
    crops = tmp_path / "crops"
    crops.mkdir()
    image = crops / "page001_panel_001.png"
    Image.new("RGB", (200, 200), "blue").save(image)
    out = tmp_path / "descriptions.json"
    calls = []

    def fake(path, key, model, series_bible=None):
        calls.append(Path(path).name)
        return {"panel_id": Path(path).stem, "file": Path(path).name,
                "width": 200, "height": 200, "ocr_text": "Come back.",
                "lines": [], "visual_description": "A boy waits in a room.",
                "source": "gemini", "ok": True}

    monkeypatch.setattr(describe, "describe_panel", fake)
    monkeypatch.setenv("GEMINI_API_KEY", "offline-placeholder")
    monkeypatch.setenv("DESCRIBE_WORKERS", "1")

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["run.py", "--input", str(crops), "--out", str(out), *args])
        runner.main()
    return crops, image, out, calls, fake, run


def test_same_size_replacement_is_stale_without_calls_or_overwrite(description_run):
    crops, image, out, calls, _, run = description_run
    run()
    before = out.read_bytes()
    run("--merge")
    assert calls == [image.name]
    Image.new("RGB", (200, 200), "red").save(image)
    Image.new("RGB", (200, 200), "green").save(crops / "page001_panel_002.png")
    with pytest.raises(SystemExit, match="stale or unknown"):
        run("--merge")
    assert out.read_bytes() == before and calls == [image.name]
    assert json_at(str(out) + ".cache.json")["panels"][image.name] == "stale"


@pytest.mark.parametrize("change", ["model", "prompt", "legacy"])
def test_description_generation_or_legacy_change_requires_review(description_run, monkeypatch, change):
    _, image, out, calls, _, run = description_run
    run()
    args = ["--merge"]
    if change == "model":
        args += ["--model", "different-model"]
    elif change == "prompt":
        monkeypatch.setattr(describe, "VISION_PROMPT", describe.VISION_PROMPT + " Changed.")
    else:
        recs = json_at(out)
        recs[0].pop("generation")
        cache.write_json(out, recs)
    before = out.read_bytes()
    with pytest.raises(SystemExit):
        run(*args)
    assert calls == [image.name] and out.read_bytes() == before


def test_explicit_failed_refresh_cannot_relabel_old_pixels_as_current(description_run, monkeypatch):
    _, image, out, _, fake, run = description_run
    run()
    original = out.read_bytes()
    Image.new("RGB", (200, 200), "red").save(image)
    def failed(*a, **k):
        return {**fake(*a, **k), "ok": False, "error": "offline failure"}
    monkeypatch.setattr(describe, "describe_panel", failed)
    with pytest.raises(SystemExit, match="incomplete"):
        run("--merge", "--force-rerun")
    assert not json_at(out)[0]["ok"]
    backups = list(out.parent.glob("descriptions.json.previous-*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert json_at(str(out) + ".cache.json")["status"] == "incomplete"


def test_usage_cap_preserves_existing_merge_records(description_run, monkeypatch):
    crops, _, out, _, fake, run = description_run
    run()
    for i in (2, 3):
        Image.new("RGB", (200, 200), "blue").save(crops / f"page001_panel_{i:03d}.png")
    def capped(path, *a, **k):
        if Path(path).stem.endswith("003"):
            raise runner.UsageCapExceeded("offline cap")
        return fake(path, *a, **k)
    monkeypatch.setattr(describe, "describe_panel", capped)
    with pytest.raises(SystemExit, match="USAGE CAP EXCEEDED"):
        run("--merge")
    assert [r["panel_id"] for r in json_at(out)] == ["page001_panel_001", "page001_panel_002"]


def test_cap_does_not_label_retained_stale_record_current(description_run, monkeypatch):
    _, image, out, _, _, run = description_run
    run()
    Image.new("RGB", (200, 200), "red").save(image)
    def capped(*a, **k):
        raise runner.UsageCapExceeded("offline cap")
    monkeypatch.setattr(describe, "describe_panel", capped)
    with pytest.raises(SystemExit, match="USAGE CAP EXCEEDED"):
        run("--merge", "--force-rerun")
    report = json_at(str(out) + ".cache.json")
    assert report["status"] == "incomplete"
    assert report["panels"][image.stem] == "stale"


def test_bad_panel_preserves_successful_progress_and_stops_description_stage(description_run, monkeypatch):
    crops, _, out, _, fake, run = description_run
    bad = crops / "page001_panel_002.png"
    bad.write_bytes(b"not an image")
    def unreadable(path, *a, **k):
        if Path(path) == bad:
            raise ValueError("unreadable panel")
        return fake(path, *a, **k)
    monkeypatch.setattr(describe, "describe_panel", unreadable)
    with pytest.raises(SystemExit, match="incomplete"):
        run()
    recs = json_at(out)
    assert len(recs) == 2 and recs[0]["ok"] and not recs[1]["ok"]
    assert "unreadable panel" in recs[1]["error"]


def test_source_changed_during_description_is_not_verified(description_run, monkeypatch):
    _, image, out, _, fake, run = description_run
    def changed(*a, **k):
        result = fake(*a, **k)
        Image.new("RGB", (200, 200), "red").save(image)
        return result
    monkeypatch.setattr(describe, "describe_panel", changed)
    with pytest.raises(SystemExit, match="incomplete"):
        run()
    assert not json_at(out)[0]["ok"]
    assert "changed during" in json_at(out)[0]["error"]


def test_script_inputs_track_evidence_context_and_effective_direct(tmp_path, monkeypatch):
    (tmp_path / "crops").mkdir()
    (tmp_path / "crops/p.png").write_bytes(b"old-pixels")
    cache.write_json(tmp_path / "descriptions.json", [{"panel_id": "p"}])
    panels = [{"panel_id": "p", "ocr_text": "Go."}]
    bible = {"characters": [{"name": "Taro"}]}
    def inputs(**kw):
        return cache.script_inputs(tmp_path, panels, kw.get("bible", bible), None,
                                   kw.get("title", "A"), kw.get("alias", "B"), kw.get("model", "M"))
    monkeypatch.setenv("DIRECT_SPEECH", "0")
    old = inputs()
    for kw in ({"bible": {}}, {"title": "C"}, {"alias": "D"}, {"model": "N"}):
        assert cache.digest(old) != cache.digest(inputs(**kw))
    monkeypatch.setenv("DIRECT_SPEECH", "1")
    assert cache.digest(old) != cache.digest(inputs())
    monkeypatch.setenv("DIRECT_SPEECH", "0")
    (tmp_path / "crops/p.png").write_bytes(b"new-pixels")
    assert old != inputs()
    # A snapshot must not mutate along with caller-owned evidence.
    panels[0]["ocr_text"] = "Changed."
    assert old["panels"][0]["ocr_text"] == "Go."


@pytest.mark.parametrize("change, expected", [("inputs", "stale"), ("script", "edited"),
                                               ("provenance", "edited"), ("missing", "incomplete"),
                                               ("legacy", "unknown"), ("malformed", "unknown")])
def test_script_cache_changes_preserve_work_and_require_explicit_rerun(tmp_path, change, expected):
    script = tmp_path / "script.txt"
    prov = tmp_path / "script.json"
    script.write_text("He returns.")
    prov.write_text("[]")
    outputs = {"script.txt": script, "script.json": prov}
    path = tmp_path / "script.cache.json"
    inputs = {"source": "old"}
    cache.record(path, inputs, outputs)
    assert cache.status(path, inputs, outputs) == "current"
    if change == "inputs":
        inputs = {"source": "new"}
    elif change == "script":
        script.write_text("My editorial revision.")
    elif change == "provenance":
        prov.write_text('[{"panel_ids": ["different"]}]')
    elif change == "missing":
        prov.unlink()
    elif change == "legacy":
        path.unlink()
    else:
        path.write_text("[]")
    before = script.read_bytes()
    assert cache.status(path, inputs, outputs) == expected
    with pytest.raises(RuntimeError, match=expected):
        cache.require_current(path, inputs, outputs, "narrate")
    assert script.read_bytes() == before


def test_voice_identity_excludes_secrets_and_tracks_speakable_text_config(tmp_path):
    beats = [{"index": 0, "text": "Hello."}]
    cfg = {"provider": "gemini", "model": "M", "voice": "Charon", "style": "Calm", "api_key": "secret"}
    old = cache.voice_inputs(beats, cfg)
    assert "secret" not in json.dumps(old)
    assert old == cache.voice_inputs(beats, {**cfg, "api_key": "rotated"})
    for key in ("provider", "model", "voice", "style"):
        assert old != cache.voice_inputs(beats, {**cfg, key: "changed"})
    assert old != cache.voice_inputs([{"index": 0, "text": "Goodbye."}], cfg)
    assert old != cache.voice_inputs([{"index": 0, "text": "Hello.", "silent": True}], cfg)


def test_rerun_archives_editorial_audio_clips_and_manifests(tmp_path):
    import pipeline_steps
    payloads = {"script.txt": b"my script", "script.json": b"[]", "script.cache.json": b"{}",
                "voice.cache.json": b"{}", "review.json": b"approved", "edits.log.jsonl": b"edit",
                "audio/beat_000.mp3": b"voice", "audio/slices/a.mp3": b"slice",
                "clips/seg_000.mp4": b"clip", "exports/v.mp4": b"approved-export"}
    for rel, data in payloads.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    removed = pipeline_steps.prepare_rerun(str(tmp_path), "narrate")
    log = json_at(tmp_path / "rerun.log.jsonl")
    archive = tmp_path / log["archive"]
    assert set(removed) == set(json_at(archive / "index.json")["paths"])
    for rel, data in payloads.items():
        if rel.startswith("exports/"):
            assert (tmp_path / rel).read_bytes() == data
        else:
            assert not (tmp_path / rel).exists()
            assert (archive / rel).read_bytes() == data


def test_partial_archive_failure_is_recoverable(tmp_path, monkeypatch):
    import pipeline_steps
    for name in ("script.txt", "script.json"):
        (tmp_path / name).write_text("original")
    real = os.replace
    def fail_second(src, dst):
        if str(src).endswith("script.json"):
            raise OSError("offline disk error")
        real(src, dst)
    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(OSError, match="disk error"):
        pipeline_steps.prepare_rerun(str(tmp_path), "narrate")
    indices = list((tmp_path / "_revisions").glob("*/index.json"))
    assert len(indices) == 1 and json_at(indices[0])["paths"] == ["script.txt"]
    assert (indices[0].parent / "script.txt").read_text() == "original"
    assert (tmp_path / "script.json").read_text() == "original"


@pytest.fixture
def ingest_fixture(tmp_path, monkeypatch):
    """Run the real ingest flow through voice; fake all service boundaries."""
    import ingest
    import narrate
    import matcher
    import panel_stats
    import series_bible
    import series_pack
    import gemini_tts
    import usage
    url = "https://asuracomic.net/series/offline-story-12345678/chapter/1"
    pdir = tmp_path / ingest.project_id(url)
    counts = {"narrate": 0, "voice": 0}
    bible = {"characters": [{"name": "Taro"}]}

    def download(url, pages):
        Image.new("RGB", (200, 200), "blue").save(Path(pages) / "page001.png")
        return [str(Path(pages) / "page001.png")]

    def split(pages, crops, **kw):
        Image.new("RGB", (200, 200), "blue").save(Path(crops) / "page001_panel_001.png")
        return 1, {"pages": 1, "panels": 1}

    def describe_subprocess(*a, **kw):
        out = pdir / "descriptions.json"
        if not out.exists():
            cache.write_json(out, [{"panel_id": "page001_panel_001", "file": "page001_panel_001.png",
                "width": 200, "height": 200, "ok": True, "role": "art",
                "ocr_text": "Come back.", "visual_description": "A boy waits in a room."}])
        return SimpleNamespace(returncode=0)

    def generate(panels, **kw):
        counts["narrate"] += 1
        return "He returns to the room.", [(panels, "He returns to the room.")]

    def synth(text, out, **kw):
        counts["voice"] += 1
        Path(out).write_bytes(b"offline-recorded-audio")

    class ReachedMatch(Exception):
        pass
    def match(*a, **kw):
        raise ReachedMatch()

    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    monkeypatch.setattr(ingest.subprocess, "run", describe_subprocess)
    monkeypatch.setattr(ingest, "_dur", lambda path: 1.)
    monkeypatch.setattr(usage, "set_job", lambda *a: None)
    monkeypatch.setattr(usage, "set_tier", lambda *a: None)
    monkeypatch.setattr(usage, "carry", lambda f: f)
    monkeypatch.setattr(panel_stats, "annotate", lambda *a: {"art": 1})
    monkeypatch.setattr(series_bible, "load_series_bible", lambda *a, **kw: bible)
    monkeypatch.setattr(series_bible, "save_series_bible", lambda *a, **kw: None)
    monkeypatch.setattr(series_pack, "load", lambda *a: {})
    monkeypatch.setattr(narrate, "generate_narration", generate)
    monkeypatch.setattr(narrate, "provenance", lambda results: [
        {"scene_id": 1, "panel_ids": ["page001_panel_001"], "text": "He returns to the room."}])
    monkeypatch.setattr(gemini_tts, "engine_for_project", lambda p: {
        "provider": "gemini", "model": "M", "voice": "Charon", "style": "Calm", "api_key": "offline"})
    monkeypatch.setattr(matcher, "match_beats_to_panels", match)
    monkeypatch.setitem(sys.modules, "scraper", SimpleNamespace(download_chapter=download, LAST_WARNING=""))
    monkeypatch.setitem(sys.modules, "splitlab", SimpleNamespace(split_into=split))
    monkeypatch.setitem(sys.modules, "server", SimpleNamespace(_synth_rest=synth))
    monkeypatch.setitem(sys.modules, "research_service", SimpleNamespace(note_suggestions=lambda *a: None))
    def run(fresh=False):
        return ingest.run_ingest(url, lambda *a: None, direct=False, fresh=fresh)
    return pdir, counts, run, ReachedMatch


def test_real_ingest_reuses_current_script_and_voice_without_generation(ingest_fixture):
    pdir, counts, run, reached = ingest_fixture
    with pytest.raises(reached):
        run()
    assert counts == {"narrate": 1, "voice": 1}
    assert (pdir / "script.cache.json").exists() and (pdir / "voice.cache.json").exists()
    with pytest.raises(reached):
        run()
    assert counts == {"narrate": 1, "voice": 1}


@pytest.mark.parametrize("change, step, state", [("evidence", "narrate", "stale"),
    ("script", "narrate", "edited"), ("legacy", "narrate", "unknown"),
    ("voice", "voice", "edited"), ("legacy_voice", "voice", "unknown")])
def test_real_ingest_stops_before_stale_generation(ingest_fixture, change, step, state):
    pdir, counts, run, reached = ingest_fixture
    with pytest.raises(reached):
        run()
    if change == "evidence":
        recs = json_at(pdir / "descriptions.json")
        recs[0]["ocr_text"] = "Stay here."
        cache.write_json(pdir / "descriptions.json", recs)
    elif change == "script":
        (pdir / "script.txt").write_text("My new editorial words.")
    elif change == "legacy":
        (pdir / "script.cache.json").unlink()
    elif change == "voice":
        (pdir / "audio/beat_000.mp3").write_bytes(b"user replacement")
    else:
        (pdir / "voice.cache.json").unlink()
    script = (pdir / "script.txt").read_bytes()
    voice = (pdir / "audio/beat_000.mp3").read_bytes()
    with pytest.raises(RuntimeError, match=f"{step} cache is {state}"):
        run()
    assert counts == {"narrate": 1, "voice": 1}
    assert (pdir / "script.txt").read_bytes() == script
    assert (pdir / "audio/beat_000.mp3").read_bytes() == voice


def test_fresh_ingest_archives_old_voice_pin_and_approved_work(ingest_fixture):
    pdir, counts, run, reached = ingest_fixture
    with pytest.raises(reached):
        run()
    (pdir / "tts.json").write_text('{"voice":"old narrator"}')
    (pdir / "review.json").write_text('{"status":"approved"}')
    original_script = (pdir / "script.txt").read_bytes()
    with pytest.raises(reached):
        run(fresh=True)
    assert counts == {"narrate": 2, "voice": 2}
    indices = list((pdir / "_revisions").glob("*/index.json"))
    assert len(indices) == 1
    archive = indices[0].parent
    assert (archive / "script.txt").read_bytes() == original_script
    assert json_at(archive / "tts.json")["voice"] == "old narrator"
    assert json_at(archive / "review.json")["status"] == "approved"
    assert not (pdir / "tts.json").exists()  # fake engine intentionally does not pin


def test_failed_ingest_download_keeps_active_sources(ingest_fixture, monkeypatch):
    pdir, counts, run, reached = ingest_fixture
    with pytest.raises(reached):
        run()
    old = {str(p.relative_to(pdir)): p.read_bytes() for p in pdir.glob("crops/*")}
    def failed(*a, **kw):
        raise RuntimeError("offline scrape failed")
    monkeypatch.setattr(sys.modules["scraper"], "download_chapter", failed)
    with pytest.raises(RuntimeError, match="scrape failed"):
        run()
    assert old == {str(p.relative_to(pdir)): p.read_bytes() for p in pdir.glob("crops/*")}
    assert counts == {"narrate": 1, "voice": 1}


@pytest.mark.parametrize("existing", [False, True])
def test_incomplete_description_stage_never_starts_narration_or_voice(ingest_fixture, monkeypatch, existing):
    import ingest
    pdir, counts, run, reached = ingest_fixture
    if existing:
        with pytest.raises(reached):
            run()
    before = {name: (pdir / name).read_bytes() for name in ("script.txt", "audio/beat_000.mp3")
              if (pdir / name).exists()}
    old_counts = dict(counts)
    def incomplete(argv, **kw):
        kw["stdout"].write("Description pass incomplete — progress preserved")
        return SimpleNamespace(returncode=1, args=argv)
    monkeypatch.setattr(ingest.subprocess, "run", incomplete)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        run()
    assert "Description pass incomplete" in exc.value.stderr
    assert counts == old_counts
    for name, contents in before.items():
        assert (pdir / name).read_bytes() == contents


def test_source_promotion_preserves_old_bytes_and_keeps_unchanged_tree(tmp_path):
    staging = tmp_path / ".source-staging-test"
    for base in (tmp_path, staging):
        for name in ("pages", "crops"):
            (base / name).mkdir(parents=True)
            (base / name / "a.png").write_bytes(b"original")
    old_inode = (tmp_path / "pages/a.png").stat().st_ino
    (staging / "crops/a.png").write_bytes(b"changed")
    revision = cache.promote_sources(tmp_path, staging)
    assert (tmp_path / "pages/a.png").stat().st_ino == old_inode
    assert (tmp_path / "crops/a.png").read_bytes() == b"changed"
    assert (tmp_path / revision / "crops/a.png").read_bytes() == b"original"
    assert json_at(tmp_path / revision / "index.json")["status"] == "complete"
    assert not staging.exists()


def test_empty_source_workspace_cannot_replace_active_source(tmp_path):
    staging = tmp_path / ".source-staging-test"
    for base in (tmp_path, staging):
        for name in ("pages", "crops"):
            (base / name).mkdir(parents=True)
    (tmp_path / "pages/a.png").write_bytes(b"original")
    (staging / "pages/a.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="empty staged crops"):
        cache.promote_sources(tmp_path, staging)
    assert (tmp_path / "pages/a.png").read_bytes() == b"original"
    assert (staging / "pages/a.png").read_bytes() == b"changed"


def test_partial_source_promotion_keeps_both_old_trees_and_pending_new_source(tmp_path, monkeypatch):
    staging = tmp_path / ".source-staging-test"
    for base, payload in ((tmp_path, b"original"), (staging, b"new")):
        for name in ("pages", "crops"):
            (base / name).mkdir(parents=True)
            (base / name / "a.png").write_bytes(payload)
    real = os.replace
    def fail_last(src, dst):
        if Path(src) == staging / "crops":
            raise OSError("offline promotion error")
        real(src, dst)
    monkeypatch.setattr(os, "replace", fail_last)
    with pytest.raises(OSError, match="promotion error"):
        cache.promote_sources(tmp_path, staging)
    indices = list((tmp_path / "_revisions").glob("source-*/index.json"))
    assert len(indices) == 1
    assert json_at(indices[0]) == {"step": "source", "paths": ["pages", "crops"], "status": "partial"}
    for name in ("pages", "crops"):
        assert (indices[0].parent / name / "a.png").read_bytes() == b"original"
    assert (tmp_path / "pages/a.png").read_bytes() == b"new"
    assert (staging / "crops/a.png").read_bytes() == b"new"
