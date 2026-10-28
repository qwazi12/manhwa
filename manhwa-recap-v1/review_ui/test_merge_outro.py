"""Merged videos drop "To be continued..." between chapters (owner, 2026-10-28)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
import range_compile as rc


def chapter(root, n, outro=True, speed=1.0, outro_path=None):
    d = root / f"s_{n}"; (d / "exports" / "_qa").mkdir(parents=True)
    name = f"final_{n}.mp4"
    cards = [{"role": "intro", "path": ""}] + ([{"role": "outro", "path": outro_path or ""}] if outro else [])
    (d / "exports" / "_qa" / (name + ".manifest.json")).write_text(json.dumps({"context": {"speed": speed, "cards": cards}}))
    return str(d), name


def test_inner_outros_are_cut_and_the_last_is_kept(tmp_path):
    chs = [(1, *chapter(tmp_path, 1, speed=1.15)), (2, *chapter(tmp_path, 2, speed=1.15)), (3, *chapter(tmp_path, 3, speed=1.15))]
    for _, d, name in chs:
        Path(d, "exports", name).write_bytes(b"x")
    seen = {}
    def run(cmd):
        seen["list"] = Path(cmd[cmd.index("-i") + 1]).read_text()
        Path(cmd[-1]).write_bytes(b"out")
    marks = rc.stitch(chs, str(tmp_path / "out" / "r.mp4"), _run=run, _probe_fn=lambda p: 100.0)
    lst = seen["list"]
    cut = round(rc.OUTRO_SECONDS / 1.15, 3)
    assert lst.count("outpoint") == 2                       # chapters 1 and 2, not 3
    end = 100 - cut - rc.CUT_MARGIN
    assert f"outpoint {end:.3f}" in lst
    assert marks == [(1, 0.0), (2, end), (3, 2 * end)]


def test_no_manifest_or_no_outro_means_no_cut(tmp_path):
    d1, n1 = chapter(tmp_path, 1, outro=False)
    d2 = tmp_path / "s_2"; (d2 / "exports").mkdir(parents=True)        # no manifest at all
    assert rc.outro_seconds(d1, n1) == 0.0
    assert rc.outro_seconds(str(d2), "final_2.mp4") == 0.0


def test_flag_off_keeps_every_outro(tmp_path):
    chs = [(1, *chapter(tmp_path, 1)), (2, *chapter(tmp_path, 2))]
    seen = {}
    def run(cmd):
        seen["list"] = Path(cmd[cmd.index("-i") + 1]).read_text(); Path(cmd[-1]).write_bytes(b"o")
    rc.stitch(chs, str(tmp_path / "o" / "r.mp4"), _run=run, _probe_fn=lambda p: 10.0, drop_inner_outros=False)
    assert "outpoint" not in seen["list"]


def _clip(path, color, secs):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=30:d={secs}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}", "-c:v", "libx264", "-g", "30",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(path)], check=True)


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode, reason="needs ffmpeg")
def test_real_videos_have_no_outro_frames_between_chapters(tmp_path):
    chs = []
    for n in (1, 2, 3):
        outro = tmp_path / f"outro{n}.mp4"; _clip(outro, "red", 1)
        d, name = chapter(tmp_path, n, outro_path=str(outro))
        story = tmp_path / f"story{n}.mp4"; _clip(story, "blue", 2)
        lst = tmp_path / f"l{n}.txt"; lst.write_text(f"file '{story}'\nfile '{outro}'\n")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                        str(Path(d, "exports", name))], check=True)
        chs.append((n, d, name))
    out = tmp_path / "range" / "r.mp4"
    marks = rc.stitch(chs, str(out))
    dur = rc._probe(str(out))
    assert abs(dur - 7.0) < 0.3, dur                     # 2 + 2 + (2 + 1 final outro)
    # sample one frame every 0.25 s: red only in the final second
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf", "fps=4,scale=1:1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    reds = [i / 4 for i in range(len(raw) // 3) if raw[3 * i] > 150 and raw[3 * i + 2] < 100]
    assert reds and min(reds) >= dur - 1.1, reds
    assert [m[0] for m in marks] == [1, 2, 3] and abs(marks[1][1] - 2.0) < 0.1


def test_restitch_replaces_only_the_video_and_keeps_details(tmp_path, monkeypatch):
    import ingest
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    pieces = []
    for n in (32, 33):
        d, name = chapter(tmp_path, n)
        os.rename(d, str(tmp_path / f"tmh_{n}")); d = str(tmp_path / f"tmh_{n}")
        Path(d, "exports", name).write_bytes(b"chapter")
        pieces.append({"project": f"tmh_{n}", "name": name})
    r = tmp_path / "tmh_ch32-33"; (r / "exports").mkdir(parents=True)
    (r / "exports" / "final_range_32-33.mp4").write_bytes(b"old video with outros")
    (r / "project.json").write_text(json.dumps({"id": "tmh_ch32-33", "kind": "range", "chapters_start": 32,
        "chapters_end": 33, "merged_from": pieces, "series": "TMH",
        "build": {"status": "done", "export": "final_range_32-33.mp4", "marks": [[32, 0], [33, 100]]}}))
    details = {"final_range_32-33.mp4": {"title": "[32-33] OWNER TITLE", "thumbnail": "t.jpg"}}
    (r / "publish.json").write_text(json.dumps(details))
    got = {}
    def fake_stitch(chs, out):
        got["chapters"] = [c[0] for c in chs]
        os.makedirs(os.path.dirname(out), exist_ok=True); Path(out).write_bytes(b"new video")
        return [(32, 0.0), (33, 97.3)]
    monkeypatch.setattr(rc, "stitch", fake_stitch)
    monkeypatch.setattr(server._mqa, "start", lambda *a: None)
    class Now:
        def __init__(self, target, daemon=None): self.t = target
        def start(self): self.t()
    import threading
    monkeypatch.setattr(threading, "Thread", Now)
    c = TestClient(server.app)
    assert c.post("/api/ranges/restitch", json={"project": "tmh_ch32-33"}).status_code == 200
    assert got["chapters"] == [32, 33]
    assert (r / "exports" / "final_range_32-33.mp4").read_bytes() == b"new video"
    assert not (r / "exports" / "_restitch").exists()
    assert json.loads((r / "publish.json").read_text()) == details              # details untouched
    assert json.loads((r / "project.json").read_text())["build"]["marks"] == [[32, 0.0], [33, 97.3]]
    (r / "publishes.json").write_text(json.dumps({"final_range_32-33.mp4": {"results": [{"status": "published"}]}}))
    assert c.post("/api/ranges/restitch", json={"project": "tmh_ch32-33"}).status_code == 409   # posted: refused
