"""A compilation is checked when it is joined (2026-11-13; before, nothing
was): pieces in rising chapter order, none repeated, all encoded alike, and
the joined file as long as its pieces add up to. Real ffmpeg, tiny clips."""
import subprocess
import sys
import os
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import range_compile as rc  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode,
                                  reason="needs ffmpeg")


def _clip(path, secs, size="320x180", rate=48000):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=blue:s={size}:r=30:d={secs}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}", "-c:v", "libx264", "-g", "30",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", str(rate), "-ac", "2", "-shortest", str(path)],
                   check=True)


def _piece(tmp, n, secs=2, **k):
    d = tmp / f"s_{n}"
    _clip(d / "exports" / f"final_{n}.mp4", secs, **k)
    return (n, str(d), f"final_{n}.mp4")


@needs_ffmpeg
def test_a_good_compilation_is_joined_and_adds_up(tmp_path):
    chs = [_piece(tmp_path, n) for n in (1, 2, 3)]
    out = tmp_path / "r" / "final_range_1-3.mp4"
    marks = rc.stitch(chs, str(out))
    assert [n for n, _t in marks] == [1, 2, 3]
    assert abs(rc._probe(str(out)) - 6.0) < 0.3
    assert abs(marks[2][1] - 4.0) < 0.2                     # chapter 3 starts after 1 and 2


@needs_ffmpeg
def test_extending_a_compilation_keeps_the_old_part_first(tmp_path):
    """Yesterday's 1-2 compilation plus new chapter 3 = previous material, then the new chapter."""
    first = [_piece(tmp_path, 1, 2), _piece(tmp_path, 2, 3)]
    old = tmp_path / "s_ch1-2" / "exports" / "final_range_1-2.mp4"
    rc.stitch(first, str(old))
    before = old.read_bytes()
    out = tmp_path / "s_ch1-3" / "exports" / "final_range_1-3.mp4"
    marks = rc.stitch([(1, str(tmp_path / "s_ch1-2"), "final_range_1-2.mp4"), _piece(tmp_path, 3, 2)], str(out))
    assert old.read_bytes() == before                       # the approved compilation is untouched
    assert abs(marks[1][1] - 5.0) < 0.2                     # new chapter starts where the old video ended
    assert abs(rc._probe(str(out)) - 7.0) < 0.3


@needs_ffmpeg
def test_a_piece_encoded_differently_is_refused_before_joining(tmp_path):
    chs = [_piece(tmp_path, 1), _piece(tmp_path, 2, size="640x360")]
    out = tmp_path / "r" / "x.mp4"
    with pytest.raises(rc.StitchCheckFailed, match="encoded differently"):
        rc.stitch(chs, str(out))
    assert not out.exists() and not Path(str(out) + ".part.mp4").exists()


def test_repeated_or_out_of_order_chapters_are_refused(tmp_path):
    chs = [(5, "a", "f5.mp4"), (5, "b", "f5b.mp4")]
    with pytest.raises(rc.StitchCheckFailed, match="out of order or repeated"):
        rc.check_pieces(chs, _sig_fn=False)
    with pytest.raises(rc.StitchCheckFailed, match="out of order or repeated"):
        rc.check_pieces([(7, "a", "f7.mp4"), (6, "b", "f6.mp4")], _sig_fn=False)
    rc.check_pieces([(6, "a", "f6.mp4"), (7, "b", "f7.mp4")], _sig_fn=False)


@needs_ffmpeg
def test_a_joined_file_that_does_not_add_up_is_refused(tmp_path, monkeypatch):
    chs = [_piece(tmp_path, n) for n in (1, 2)]
    out = tmp_path / "r" / "x.mp4"
    out.parent.mkdir()
    out.write_bytes(b"the previous good file")
    real = rc._probe
    # the pieces claim 60 s each; the joined file is really ~4 s: as if a piece were cut short
    monkeypatch.setattr(rc, "_probe", lambda p: real(p) if p.endswith(".part.mp4") else 60.0)
    with pytest.raises(rc.StitchCheckFailed, match="add up to"):
        rc.stitch(chs, str(out))
    assert out.read_bytes() == b"the previous good file"    # output left as it was
    assert not Path(str(out) + ".part.mp4").exists()


def _decode_errors(path):
    """What final-media QA runs: a full decode of picture and sound; any
    message means broken timestamps."""
    v = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-an",
                        "-f", "null", "-"], capture_output=True, text=True)
    a = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path), "-map", "0:a:0", "-vn",
                        "-f", "null", "-"], capture_output=True, text=True)
    return (v.stderr + a.stderr).strip()


def _studio_chapter(tmp, n, story_secs, outro_secs=2.6):
    """A chapter export built the studio's way: story clip + "To be continued"
    card, joined by media_concat, with the manifest that records the card."""
    import json
    sys.path.insert(0, os.path.dirname(HERE))
    from media_concat import concatenate
    d = tmp / f"s_{n}"
    (d / "exports" / "_qa").mkdir(parents=True)
    story, outro = tmp / f"story{n}.mp4", tmp / f"outro{n}.mp4"
    for path, secs, src in ((story, story_secs, "testsrc2"), (outro, outro_secs, "color=c=red")):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{src}{':' if '=' in src else '='}s=320x180:r=30:d={secs}",
                        "-f", "lavfi", "-i", f"sine=frequency={300 + 100 * n}:duration={secs}", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(path)], check=True)
    name = f"final_{n}.mp4"
    concatenate([story, outro], d / "exports" / name)
    (d / "exports" / "_qa" / (name + ".manifest.json")).write_text(json.dumps(
        {"context": {"speed": 1.0, "cards": [{"role": "outro", "path": str(outro)}]}}))
    return (n, str(d), name), rc._video_seconds(str(story))


@needs_ffmpeg
def test_joins_have_continuous_timestamps_and_keep_every_story_frame(tmp_path, monkeypatch):
    """The old packet-copy join left two pictures with one timestamp at every
    boundary: 30 of 30 merged exports checked on production failed final-media
    QA for it (2026-11-13)."""
    built = [_studio_chapter(tmp_path, n, 7.3 + 0.37 * n) for n in (1, 2, 3, 4)]
    chs = [b[0] for b in built]
    story = [b[1] for b in built]
    monkeypatch.delenv("STITCH_ENGINE", raising=False)
    out = tmp_path / "r" / "final_range_1-4.mp4"
    marks = rc.stitch(chs, str(out))
    assert _decode_errors(out) == ""
    # each chapter starts exactly where the story before it ends: nothing dropped, no card frames kept
    for i in (1, 2, 3):
        assert abs(marks[i][1] - sum(story[:i])) < 0.02, (i, marks[i][1], sum(story[:i]))
    assert abs(rc._probe(str(out)) - (sum(story) + 2.6)) < 0.1      # only the last card is kept

    # the old join, for the record, on the same pieces
    monkeypatch.setenv("STITCH_ENGINE", "copy")
    old = tmp_path / "r" / "old.mp4"
    rc.stitch(chs, str(old))
    assert "non monotonically increasing dts" in _decode_errors(old)
