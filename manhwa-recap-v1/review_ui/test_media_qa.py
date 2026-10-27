"""M1 real-media regressions. Generated local signals, no network or AI calls."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

import media_qa as qa
import media_audio
from audio_paths import beat_audio_path


def run(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *map(str, args)], check=True)


def audio(path, kind="tone", seconds=2):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    source = f"sine=frequency=440:sample_rate=16000:duration={seconds}" if kind == "tone" else f"anullsrc=r=16000:cl=mono:d={seconds}"
    run("-f", "lavfi", "-i", source, "-b:a", "192k", path)


def video(path, speech, seconds=2, speed=1):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    run("-f", "lavfi", "-i", f"color=c=blue:s=64x64:r=10:d={seconds}", "-i", speech,
        "-filter:v", f"setpts=PTS/{speed}", "-filter:a", f"atempo={speed}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", seconds / speed, path)


def project(tmp_path, kind="tone", speed=1, filename=None, seconds=2):
    d = tmp_path / "chapter_1"
    (d / "crops").mkdir(parents=True)
    (d / "exports").mkdir()
    pic = d / "crops/p.png"
    Image.new("RGB", (64, 64), "blue").save(pic)
    a = d / "audio" / (filename or "beat_000.mp3")
    audio(a, kind, seconds)
    b = {"index": 0, "start": 10, "end": 10 + seconds, "text": "The hero entered the castle."}
    if filename:
        b["file"] = filename
    s = {"seg_index": 0, "panel_id": "p", "panel_file": str(pic), "user_included": True,
         "start": 10, "end": 10 + seconds, "dur": seconds, "beats": [b], "clip": "clips/seg_000.mp4"}
    (d / "segments.json").write_text(json.dumps([s]))
    (d / "project.json").write_text('{}')
    video(d / s["clip"], a, seconds)
    video(d / "exports/final.mp4", a, seconds, speed)
    qa.capture_export(d, "final.mp4", speed=speed)
    return d, a


def test_late_qa_writes_do_not_resurrect_a_deleted_project(tmp_path):
    d, _ = project(tmp_path)
    deleted = tmp_path / "deleted-chapter"
    d.rename(deleted)
    original_video = (deleted / "exports/final.mp4").read_bytes()

    # A queued inspector or range-build completion may arrive after Delete.
    qa.inspect(d, "final.mp4")
    qa.record_incomplete(d, "final.mp4", "late checker error")
    qa.capture_export(d, "final.mp4")

    assert not d.exists()
    assert (deleted / "exports/final.mp4").read_bytes() == original_video
    deleted.rename(d)  # the original identity is still free for Restore
    assert (d / "project.json").exists()


@pytest.mark.parametrize("filename", [None, "custom.mp3", "slices/b000_a.mp3"])
def test_standard_explicit_and_slice_audio_paths(tmp_path, filename):
    d, a = project(tmp_path, filename=filename)
    b = json.loads((d / "segments.json").read_text())[0]["beats"][0]
    assert Path(beat_audio_path(d / "audio", b)) == a
    assert qa.inspect(d, "final.mp4")["status"] == "passed"
    import chapter_qa
    assert chapter_qa.check(str(tmp_path), d.name)["status"] == "ok"


@pytest.mark.parametrize("seconds", [.5, 3])
def test_silent_expected_narration_is_not_a_valid_timeline_pass(tmp_path, seconds):
    d, a = project(tmp_path, kind="silence", seconds=seconds)
    result = qa.inspect(d, "final.mp4")
    assert result["status"] == "failed"
    assert any(f["rule"] == "narration-silent" and f["beat"] == 0 for f in result["findings"])
    import chapter_qa
    assert chapter_qa.check(str(tmp_path), d.name)["status"] == "bad"


def test_audible_short_file_is_not_called_silence(tmp_path):
    d, a = project(tmp_path, seconds=.5)
    assert a.stat().st_size < 20000
    assert qa.inspect(d, "final.mp4")["status"] == "passed"
    import chapter_qa
    assert chapter_qa.check(str(tmp_path), d.name)["status"] == "ok"


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_broken_voice_input_is_visible(tmp_path, fault):
    d, a = project(tmp_path)
    if fault == "missing":
        a.unlink()
    else:
        a.write_bytes(b"not audio")
    result = qa.inspect(d, "final.mp4")
    assert result["status"] == "failed"
    assert any(f["rule"] == "narration-broken" for f in result["findings"])


def test_corrupt_mp4_and_missing_audio_stream_fail(tmp_path):
    d, _ = project(tmp_path)
    final = d / "exports/final.mp4"
    final.write_bytes(b"broken mp4")
    assert any(f["rule"] == "export-broken" for f in qa.inspect(d, "final.mp4")["findings"])
    run("-f", "lavfi", "-i", "color=s=64x64:d=2", "-an", final)
    assert qa.inspect(d, "final.mp4")["status"] == "failed"


def test_music_energy_alone_does_not_satisfy_expected_voice(tmp_path):
    d, a = project(tmp_path)
    other = tmp_path / "music.wav"
    run("-f", "lavfi", "-i", "sine=frequency=1500:duration=2:sample_rate=16000", other)
    video(d / "exports/final.mp4", other)
    result = qa.inspect(d, "final.mp4")
    assert any(f["rule"] == "voice-unconfirmed" for f in result["findings"])
    mixed = tmp_path / "mixed.wav"
    run("-i", a, "-i", other, "-filter_complex", "[1:a]volume=.15[m];[0:a][m]amix=inputs=2", mixed)
    video(d / "exports/final.mp4", mixed)
    assert qa.inspect(d, "final.mp4")["status"] == "passed"


def test_real_pause_and_intent_are_preserved(tmp_path):
    d, a = project(tmp_path, seconds=4)
    run("-f", "lavfi", "-i", "aevalsrc=if(between(t\\,1\\,3)\\,0\\,0.1*sin(440*2*PI*t)):s=16000:d=4", a)
    seg = json.loads((d / "segments.json").read_text())
    seg[0]["intentional_pause"] = True
    (d / "segments.json").write_text(json.dumps(seg))
    video(d / seg[0]["clip"], a, seconds=4)
    video(d / "exports/final.mp4", a, seconds=4)
    qa.capture_export(d, "final.mp4")
    result = qa.inspect(d, "final.mp4")
    assert result["status"] == "passed"
    assert not any(f["rule"] == "voice-pause" for f in result["findings"])
    seg[0]["intentional_pause"] = False
    (d / "segments.json").write_text(json.dumps(seg))
    qa.capture_export(d, "final.mp4")
    assert any(f["rule"] == "voice-pause" for f in qa.inspect(d, "final.mp4")["findings"])


@pytest.mark.parametrize("change", ["export", "text", "offset", "audio", "panel", "renderer"])
def test_exact_export_and_render_input_freshness(tmp_path, monkeypatch, change):
    d, a = project(tmp_path)
    assert qa.inspect(d, "final.mp4")["fresh"]
    if change == "export":
        with (d / "exports/final.mp4").open("ab") as f:
            f.write(b"changed")
    elif change in ("text", "offset"):
        seg = json.loads((d / "segments.json").read_text())
        seg[0]["beats"][0]["text" if change == "text" else "start"] = "Other words" if change == "text" else 10.2
        (d / "segments.json").write_text(json.dumps(seg))
    elif change == "audio":
        audio(a, "silence")
    elif change == "panel":
        Image.new("RGB", (64, 64), "red").save(d / "crops/p.png")
    else:
        monkeypatch.setenv("FAST_RENDER_CRF", "30")
    assert qa.status(d, "final.mp4")["status"] == "stale"


def test_speed_card_and_selected_segment_timestamp_mapping(tmp_path):
    d, a = project(tmp_path, speed=1.25)
    manifest = qa._read(qa._paths(d, "final.mp4")[0])
    assert manifest["events"][0]["start"] == 0
    assert manifest["events"][0]["end"] == pytest.approx(media_audio.duration(a) / 1.25)
    assert qa.inspect(d, "final.mp4")["status"] == "passed"
    card_audio = tmp_path / "card.wav"
    audio(card_audio, "silence", 1)
    card = tmp_path / "intro.mp4"
    video(card, card_audio, 1)
    segs = json.loads((d / "segments.json").read_text())
    segs.insert(0, dict(segs[0], seg_index=99, user_included=False, dur=20))
    (d / "segments.json").write_text(json.dumps(segs))
    listing = tmp_path / "cards.txt"
    listing.write_text(f"file '{card}'\nfile '{d}/clips/seg_000.mp4'\nfile '{card}'\n")
    run("-f", "concat", "-safe", "0", "-i", listing,
        "-filter:v", "setpts=PTS/1.25", "-filter:a", "atempo=1.25",
        "-c:v", "libx264", "-c:a", "aac", d / "exports/final.mp4")
    manifest = qa.capture_export(d, "final.mp4", speed=1.25, cards=[("intro", card), ("outro", card)])
    assert len(manifest["events"]) == 1
    assert manifest["events"][0]["start"] == pytest.approx(media_audio.duration(card) / 1.25)
    assert qa.inspect(d, "final.mp4")["status"] == "passed"


def test_range_has_its_own_qa_and_component_offsets(tmp_path):
    a, _ = project(tmp_path / "a")
    b, _ = project(tmp_path / "b")
    d = tmp_path / "range"
    (d / "exports").mkdir(parents=True)
    listing = tmp_path / "concat.txt"
    listing.write_text(f"file '{a}/exports/final.mp4'\nfile '{b}/exports/final.mp4'\n")
    run("-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", d / "exports/final.mp4")
    manifest = qa.capture_export(d, "final.mp4", components=[(a, "final.mp4"), (b, "final.mp4")])
    assert manifest["events"][1]["start"] == pytest.approx(media_audio.duration(a / "exports/final.mp4"))
    assert qa.inspect(d, "final.mp4")["status"] == "passed"
    with (b / "exports/final.mp4").open("ab") as f:
        f.write(b"replacement")
    assert qa.status(d, "final.mp4")["status"] == "stale"


def test_legacy_exports_are_incomplete_and_enforcement_defaults_off(tmp_path, monkeypatch):
    d, _ = project(tmp_path)
    qa._paths(d, "final.mp4")[0].unlink()
    assert qa.inspect(d, "final.mp4")["status"] == "incomplete"
    monkeypatch.delenv("MEDIA_QA_ENFORCE", raising=False)
    assert qa.blockers(d, "final.mp4") == []
    monkeypatch.setenv("MEDIA_QA_ENFORCE", "1")
    assert qa.blockers(d, "final.mp4")


def test_fresh_media_needs_matching_human_approval(tmp_path, monkeypatch):
    d, _ = project(tmp_path)
    qa.inspect(d, "final.mp4")
    monkeypatch.setenv("MEDIA_QA_ENFORCE", "1")
    assert qa.blockers(d, "final.mp4")
    assert not qa.blockers(d, "final.mp4", {"media_identity": qa.identity(d, "final.mp4")})
    (d / "exports/final.mp4").write_bytes(b"changed")
    assert qa.blockers(d, "final.mp4")


def test_direct_social_worker_and_schedule_share_disabled_by_default_gate(tmp_path, monkeypatch):
    import server
    import youtube
    d, a = project(tmp_path)
    audio(a, "silence")
    qa.inspect(d, "final.mp4")
    calls = []
    backend = SimpleNamespace(accounts_status=lambda _: {
        "configured": True, "can_publish": True,
        "accounts": [{"account_id": "test:youtube", "active": True}]},
        upload_video_post=lambda *a, **k: calls.append(a))
    monkeypatch.setattr(server, "_get_publish_backend", lambda: ("fake", backend))
    monkeypatch.setattr(server, "project_dir_for", lambda _: str(d))
    monkeypatch.setattr(server, "publish_defaults", lambda _: {"title": "test", "privacy": "private", "targets": ["test:youtube"]})
    monkeypatch.setattr(server, "validate_publish", lambda *a: [])
    monkeypatch.setattr(server, "_persist_job", lambda *a: None)
    monkeypatch.setattr(server, "JOBS", {"check": {}})
    monkeypatch.setattr(youtube, "account_status", lambda _: {"can_upload": True})
    server.save_reviews(str(d), {"final.mp4": {"status": "approved", "media_identity": qa.identity(d, "final.mp4")}})
    monkeypatch.delenv("MEDIA_QA_ENFORCE", raising=False)
    assert server.upload_eligibility(str(d), "final.mp4")["ready"]
    assert server.publish_eligibility(str(d), "final.mp4")["ready"]
    monkeypatch.setenv("MEDIA_QA_ENFORCE", "1")
    assert not server.upload_eligibility(str(d), "final.mp4")["ready"]
    assert not server.publish_eligibility(str(d), "final.mp4")["ready"]
    assert server._local_post_blockers("chapter_1", "final.mp4")
    assert not server.publish_readiness(str(d), "final.mp4")["ready"]
    server._run_publish_job("check", str(d), "final.mp4")
    assert not calls
    assert server.JOBS["check"]["status"] == "error"


def test_full_scale_samples_produce_clipping_finding(tmp_path):
    d, a = project(tmp_path)
    # An uncompressed flat-topped signal avoids relying on lossy codec peaks.
    clipped = d / "audio/clipped.wav"
    run("-f", "lavfi", "-i", "aevalsrc=sgn(sin(2*PI*100*t)):s=16000:d=2", clipped)
    seg = json.loads((d / "segments.json").read_text())
    seg[0]["beats"][0]["file"] = "clipped.wav"
    (d / "segments.json").write_text(json.dumps(seg))
    qa.capture_export(d, "final.mp4")
    assert any(f["rule"] == "voice-clipping" and f["severity"] == "critical"
               for f in qa.inspect(d, "final.mp4")["findings"])


def test_real_server_export_records_post_speed_media_without_paid_preparation(tmp_path, monkeypatch):
    import server
    import ingest
    d, _ = project(tmp_path)
    monkeypatch.setattr(server, "active_project_dir", lambda: str(d))
    monkeypatch.setattr(server, "load_segments", lambda: json.loads((d / "segments.json").read_text()))
    monkeypatch.setattr(server, "load_review", lambda: {})
    monkeypatch.setattr(ingest, "parse_series_chapter", lambda _: ("", ""))
    result = server._do_export(1.25)
    manifest = qa._read(qa._paths(d, result["output"])[0])
    assert manifest["context"]["speed"] == 1.25
    assert manifest["export_digest"] == qa.file_digest(d / "exports" / result["output"])
    assert qa.inspect(d, result["output"])["status"] == "passed"


def test_overlapping_narration_is_a_technical_failure(tmp_path):
    d, _ = project(tmp_path)
    segs = json.loads((d / "segments.json").read_text())
    segs[0]["beats"].append(dict(segs[0]["beats"][0], index=1, file="beat_000.mp3", start=10.5, end=12.5))
    (d / "segments.json").write_text(json.dumps(segs))
    qa.capture_export(d, "final.mp4")
    assert any(f["rule"] == "voice-overlap" for f in qa.inspect(d, "final.mp4")["findings"])


def test_renderer_staging_uses_safe_asset_names_and_refuses_missing_audio(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(qa.RECAP) / "hyperframes"))
    import render_segments as renderer
    d, a = project(tmp_path, filename="slices/example.mp3")
    seg = json.loads((d / "segments.json").read_text())[0]
    seg["beats"][0]["file"] = str(a)
    monkeypatch.setattr(renderer, "ASSETS", str(tmp_path / "assets"))
    (tmp_path / "assets").mkdir()
    renderer.stage_assets(seg, str(d / "audio"))
    assert list((tmp_path / "assets").glob("external_*.mp3"))
    assert qa.file_digest(a)
    a.unlink()
    with pytest.raises(RuntimeError, match="audio missing"):
        renderer.audio_plan(seg, str(d / "audio"))


def test_digest_memo_survives_a_chapter_sized_pass_and_stays_bounded(tmp_path, monkeypatch):
    # A Home load fingerprints ~9k files; the old 4,096 cap cleared the memo
    # mid-pass, so every request re-hashed everything (7.6 s, 2026-10-27).
    files = []
    for i in range(5000):
        p = tmp_path / f"f{i}.bin"
        p.write_bytes(str(i).encode())
        files.append(p)
    qa._HASH_CACHE.clear()
    first = [qa.file_digest(p) for p in files]
    hashed = []
    real = qa.hashlib.sha256
    monkeypatch.setattr(qa.hashlib, "sha256", lambda *a: hashed.append(1) or real(*a))
    assert [qa.file_digest(p) for p in files] == first
    assert hashed == []                                   # second pass: all memo hits
    files[7].write_bytes(b"edited")                       # a changed file is re-hashed
    assert qa.file_digest(files[7]) != first[7] and hashed == [1]
    monkeypatch.setattr(qa, "_HASH_CACHE_MAX", 100)
    qa.file_digest(files[8].with_name("new.bin")) if files[8].with_name("new.bin").write_bytes(b"x") else None
    assert len(qa._HASH_CACHE) <= 100                     # bounded, oldest evicted


def test_digest_memo_survives_a_restart_without_rehashing(tmp_path, monkeypatch):
    memo = tmp_path / "memo.json"
    monkeypatch.setattr(qa, "_MEMO_PATH", str(memo))
    monkeypatch.setattr(qa, "_MEMO", {"loaded": True, "dirty": 0, "saved_at": 0.0})
    qa._HASH_CACHE.clear()
    files = [tmp_path / f"a{i}.png" for i in range(20)]
    for i, p in enumerate(files): p.write_bytes(bytes([i]) * 100)
    digests = [qa.file_digest(p) for p in files]
    qa.flush_memo()
    assert memo.exists()
    # "restart": empty memory, memo not yet loaded
    qa._HASH_CACHE.clear()
    monkeypatch.setattr(qa, "_MEMO", {"loaded": False, "dirty": 0, "saved_at": 0.0})
    hashed = []
    real = qa.hashlib.sha256
    monkeypatch.setattr(qa.hashlib, "sha256", lambda *a: hashed.append(1) or real(*a))
    assert [qa.file_digest(p) for p in files] == digests and hashed == []
    files[3].write_bytes(b"changed after restart")          # still detected
    assert qa.file_digest(files[3]) != digests[3] and hashed == [1]


def test_corrupt_memo_file_is_ignored(tmp_path, monkeypatch):
    memo = tmp_path / "memo.json"
    memo.write_text("{not json")
    monkeypatch.setattr(qa, "_MEMO_PATH", str(memo))
    monkeypatch.setattr(qa, "_MEMO", {"loaded": False, "dirty": 0, "saved_at": 0.0})
    qa._HASH_CACHE.clear()
    f = tmp_path / "x.bin"; f.write_bytes(b"x")
    assert qa.file_digest(f) == qa.hashlib.sha256(b"x").hexdigest()
