"""Real AAC boundary regressions, local generated media and no service calls."""
from pathlib import Path
import subprocess

import pytest
import media_audio
from media_concat import concatenate


def run(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)], check=True)


def shot(path, frequency=440, seconds=.3):
    run("-f", "lavfi", "-i", f"color=c=blue:s=64x64:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-video_track_timescale", "15360",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", seconds, path)


@pytest.mark.parametrize("count", [1, 60])
def test_independent_aac_padding_never_accumulates(tmp_path, count):
    a, b = tmp_path / "a's shot.mp4", tmp_path / "b.mp4"
    shot(a, 440)
    shot(b, 1500, .4)
    entries = [a if i % 2 == 0 else b for i in range(count)]
    before = [p.read_bytes() for p in (a, b)]
    final = tmp_path / "final.mp4"
    concatenate(entries, final)
    info = media_audio.probe(final)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    expected = sum(.3 if p == a else .4 for p in entries)
    assert float(video["start_time"]) == pytest.approx(0, abs=.001)
    assert float(video["duration"]) == pytest.approx(expected, abs=.001)
    assert float(audio["duration"]) == pytest.approx(expected, abs=.025)
    decoded = media_audio.measure(final)
    references = {a: media_audio.measure(a), b: media_audio.measure(b)}
    cursor = 0
    for p in entries:
        assert media_audio.correspondence(references[p], decoded, cursor) > .95
        cursor += .3 if p == a else .4
    assert [p.read_bytes() for p in (a, b)] == before
    assert not list(tmp_path.glob(".concat-*"))


def test_broken_input_does_not_replace_existing_export(tmp_path):
    a = tmp_path / "a.mp4"
    shot(a)
    final = tmp_path / "final.mp4"
    final.write_bytes(b"previous completed export")
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"corrupt")
    with pytest.raises(subprocess.CalledProcessError):
        concatenate([a, broken], final)
    assert final.read_bytes() == b"previous completed export"
    with pytest.raises(ValueError, match="replace"):
        concatenate([a], a)


def test_missing_audio_and_different_picture_streams_are_refused(tmp_path):
    a, silent = tmp_path / "a.mp4", tmp_path / "silent.mp4"
    shot(a)
    run("-f", "lavfi", "-i", "color=s=64x64:r=30:d=0.3", "-an", silent)
    with pytest.raises(ValueError, match="soundtrack"):
        concatenate([a, silent], tmp_path / "final.mp4")
    other = tmp_path / "other.mp4"
    run("-i", a, "-vf", "scale=128:64", "-c:a", "copy", other)
    with pytest.raises(ValueError, match="do not match"):
        concatenate([a, other], tmp_path / "final.mp4")
