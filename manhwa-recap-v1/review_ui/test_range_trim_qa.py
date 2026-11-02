"""Merged videos: QA must follow the file as stitched (2026-11-01 audit).
Stitching cuts each inner chapter's outro; QA used full chapter lengths, and
merging a merge kept that merge's own outro mid-video."""
import json

import media_qa
import range_compile as rc


def _manifest(pdir, name, ctx, events=()):
    q = pdir / "exports" / "_qa"
    q.mkdir(parents=True, exist_ok=True)
    (pdir / "exports" / name).write_bytes(b"x")
    (q / (name + ".manifest.json")).write_text(json.dumps({"context": ctx, "events": list(events)}))


def test_kept_seconds_from_marks():
    assert rc.kept_seconds([(1, 0.0), (2, 97.5), (3, 200.0)], 300.0) == [97.5, 102.5, 100.0]


def test_merge_of_a_merge_cuts_the_last_chapters_outro(tmp_path):
    ch = tmp_path / "s_5"
    _manifest(ch, "c.mp4", {"speed": 1.25, "cards": [{"role": "outro", "path": ""}]})
    rng = tmp_path / "s_ch1-5"
    _manifest(rng, "final_range_1-5.mp4", {"kind": "range", "components": [{"project": str(ch), "name": "c.mp4"}]})
    assert rc.outro_seconds(str(rng), "final_range_1-5.mp4") == round(rc.OUTRO_SECONDS / 1.25, 3)


def test_capture_uses_kept_lengths_and_drops_cut_events(tmp_path, monkeypatch):
    a, b = tmp_path / "s_1", tmp_path / "s_2"
    ev = lambda s, e: {"beat": 0, "start": s, "end": e}
    _manifest(a, "a.mp4", {}, [ev(1, 2), ev(98, 99)])     # 98 s: inside the cut outro
    _manifest(b, "b.mp4", {}, [ev(1, 2)])
    monkeypatch.setattr(media_qa.media_audio, "duration", lambda p: 100.0)
    r = tmp_path / "s_ch1-2"
    (r / "exports").mkdir(parents=True)
    (r / "exports" / "m.mp4").write_bytes(b"x")
    media_qa.capture_export(str(r), "m.mp4", components=[(str(a), "a.mp4", 97.0), (str(b), "b.mp4", 100.0)])
    m = json.loads((r / "exports" / "_qa" / "m.mp4.manifest.json").read_text())
    assert m["expected_duration"] == 197.0
    assert [e["start"] for e in m["events"]] == [1, 98.0]   # a's 1 s, then b's 1 s at 97+1
