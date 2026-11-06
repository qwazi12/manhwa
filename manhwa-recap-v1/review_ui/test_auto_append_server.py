"""Auto-append end to end on a temp library: posted 20-21 + rendered 22, 23
→ 20-23 in Needs review with the posted title/tags/thumbnail; then 24
finishes → 20-24 replaces the waiting 20-23."""
import io
import json
import os
import time

import pytest
from PIL import Image

import ingest
import range_compile as rc
import server
import thumbnail as tb
import yt_api


def _wait(root, pid):
    for _ in range(200):
        try:
            m = json.load(open(os.path.join(root, pid, "project.json")))
        except OSError:
            m = {}
        if (m.get("build") or {}).get("status") in ("done", "error"):
            return m
        time.sleep(0.05)
    raise AssertionError(f"{pid} never finished")


def _chapter(tmp_path, n):
    d = tmp_path / f"hound_{n}"
    (d / "exports").mkdir(parents=True)
    (d / "exports" / "final.mp4").write_bytes(b"v")
    (d / "project.json").write_text(json.dumps({"id": f"hound_{n}", "series": "Hound", "series_id": "hound",
                                                "chapter": str(n)}))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "_yt_root", lambda: os.path.join(root, "_yt"))
    monkeypatch.setattr(server, "_prepare_publish", lambda *a, **k: None)
    monkeypatch.setattr(server, "api_thumbcopilot_generate", lambda *a, **k: None)
    monkeypatch.setattr(server, "_drive_copy_later", lambda *a, **k: None)
    monkeypatch.setattr(server._mqa, "start", lambda *a, **k: None)
    monkeypatch.setattr(yt_api, "configured", lambda: False)
    monkeypatch.setattr(rc, "stitch", lambda chs, out: (open(out, "wb").write(b"v"),
                                                         [(n, 600.0 * i) for i, (n, _p, _x) in enumerate(chs)])[1])
    monkeypatch.setattr(rc, "_probe", lambda path: 600.0)
    monkeypatch.setenv("AUTO_APPEND", "1")
    # the posted merged video 20-21
    p = tmp_path / "hound_ch20-21"
    (p / "exports").mkdir(parents=True)
    (p / "exports" / "final_range_20-21.mp4").write_bytes(b"v")
    (p / "project.json").write_text(json.dumps({
        "id": "hound_ch20-21", "kind": "range", "series": "Hound", "series_id": "hound",
        "chapters_start": 20, "chapters_end": 21, "chapter": "20-21",
        "build": {"status": "done", "export": "final_range_20-21.mp4", "marks": [[20, 0.0], [21, 300.0]]}}))
    (p / "publishes.json").write_text(json.dumps({"final_range_20-21.mp4": {"results": [
        {"status": "published", "network": "youtube", "url": "https://www.youtube.com/watch?v=abcdefghijk",
         "published_at": 100}]}}))
    (p / server.PUBLISH_NAME).write_text(json.dumps({"final_range_20-21.mp4": {
        "title": "[20-21] Hound Returns For VENGEANCE | Flamingo Recap",
        "tags": ["hound chapter 20-21", "hound chapters 20 to 21", "hound chapter 3", "manhwa recap"]}}))
    art = Image.new("RGB", (1280, 720), (20, 40, 200))
    art.paste((255, 182, 57), (28, 28, 220, 90))               # the posted "CH 20-21" badge
    buf = io.BytesIO()
    art.save(buf, "JPEG")
    tb.save(str(p), "final_range_20-21.mp4", buf.getvalue())
    for n in (22, 23):
        _chapter(tmp_path, n)
    return root, tmp_path


def test_new_chapters_join_the_posted_video_with_its_seo(lib):
    root, tmp_path = lib
    plans = server._auto_append_pass()
    d = next(x for x in plans if x["series"] == "hound")
    assert d["action"] == "build" and (d["start"], d["end"]) == (20, 23), d
    m = _wait(root, "hound_ch20-23")
    assert m["build"]["status"] == "done", m
    md = server.load_publish(os.path.join(root, "hound_ch20-23"))["final_range_20-23.mp4"]
    assert md["title"] == "[20-23] Hound Returns For VENGEANCE | Flamingo Recap"
    tags = [x.lower() for x in md["tags"]]
    assert "hound chapter 20-23" in tags and "hound chapters 20 to 23" in tags
    assert "hound chapter 3" not in tags and len(tags) == len(set(tags))
    assert "• 0:00 - Chapter 20\n• 5:00 - Chapter 21\n• 10:00 - Chapter 22\n• 20:00 - Chapter 23" in md["description"]
    thumb = tb.path_for(os.path.join(root, "hound_ch20-23"), "final_range_20-23.mp4")
    im = Image.open(thumb).convert("RGB")
    r, g, b = im.getpixel((40, 45))
    assert r > 200 and 140 < g < 220 and b < 120        # badge kept the posted colour
    # chapters are now inside the merge, and nothing more to do
    assert server.load_reviews(os.path.join(root, "hound_22"))["final.mp4"]["merged_into"] == "hound_ch20-23"
    again = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert again["action"] == "none"

    # chapter 24 finishes: 20-24 replaces the waiting 20-23, which is moved aside
    _chapter(tmp_path, 24)
    d2 = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d2["action"] == "build" and (d2["start"], d2["end"]) == (20, 24) and d2["retire"] == ["hound_ch20-23"]
    m2 = _wait(root, "hound_ch20-24")
    assert m2["build"]["status"] == "done"
    for _ in range(100):
        if not os.path.isdir(os.path.join(root, "hound_ch20-23")):
            break
        time.sleep(0.05)
    assert not os.path.isdir(os.path.join(root, "hound_ch20-23"))
    assert not any(x.startswith("_unmerged_") for x in os.listdir(root))          # nothing hidden fills the disk
    assert any(x.startswith("hound_ch20-23__") for x in os.listdir(os.path.join(root, server.TRASH_DIR)))
    assert any(i["name"].startswith("hound_ch20-23__") for i in server.api_trash()["items"])
    for n in (22, 23, 24):
        assert server.load_reviews(os.path.join(root, f"hound_{n}"))["final.mp4"]["merged_into"] == "hound_ch20-24"


def test_off_switch(lib, monkeypatch):
    monkeypatch.setenv("AUTO_APPEND", "0")
    assert server._auto_append_pass() == []


def test_restore_a_posted_video_file(lib, tmp_path):
    import shutil
    import subprocess
    from fastapi.testclient import TestClient
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    root, _ = lib
    clip = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x36:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
    c = TestClient(server.app)
    url = "/api/export/restore?project=hound_ch20-21&name=final_range_20-21_yt.mp4"
    r = c.post(url, content=clip.read_bytes())
    assert r.status_code == 200, r.text
    assert os.path.getsize(os.path.join(root, "hound_ch20-21", "exports", "final_range_20-21_yt.mp4")) > 0
    assert c.post(url, content=clip.read_bytes()).status_code == 409            # never overwrites
    assert c.post("/api/export/restore?project=hound_22&name=x.mp4",
                  content=clip.read_bytes()).status_code == 409                  # not posted
    assert c.post("/api/export/restore?project=hound_ch20-21&name=y.mp4",
                  content=b"not a video").status_code == 422
    assert not os.path.exists(os.path.join(root, "hound_ch20-21", "exports", "y.mp4"))


def test_restore_a_posted_chapter_whose_folder_is_gone(lib, tmp_path):
    import shutil
    import subprocess
    from fastapi.testclient import TestClient
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    root, _ = lib
    clip = tmp_path / "in19.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x36:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
    c = TestClient(server.app)
    base = "/api/export/restore?name=final_range_19_yt.mp4&series_id=hound&chapter=19"
    assert c.post(base, content=clip.read_bytes()).status_code == 400                    # no video id
    assert c.post(base.replace("hound", "nope") + "&video_id=abcdefghijk",
                  content=clip.read_bytes()).status_code == 404                          # unknown series
    assert c.post(base + "&video_id=abcdefghijk", content=b"junk").status_code == 422
    assert not os.path.exists(os.path.join(root, "hound_ch19-19"))                       # nothing half-made
    r = c.post(base + "&video_id=abcdefghijk", content=clip.read_bytes())
    assert r.status_code == 200, r.text
    pdir = os.path.join(root, "hound_ch19-19")
    m = json.load(open(os.path.join(pdir, "project.json")))
    assert m["kind"] == "range" and m["series"] == "Hound" and m["build"]["marks"] == [[19, 0.0]]
    assert server._posted_names(pdir)["final_range_19_yt.mp4"]["video_id"] == "abcdefghijk"
    assert c.post(base + "&video_id=abcdefghijk", content=clip.read_bytes()).status_code == 409


def test_posted_merge_whose_file_is_gone_is_built_from_its_chapters(lib, tmp_path):
    root, tmp_path = lib
    p = tmp_path / "hound_ch20-21"
    os.remove(p / "exports" / "final_range_20-21.mp4")          # cleared before Drive archiving
    for n in (20, 21):
        _chapter(tmp_path, n)
    m = json.loads((p / "project.json").read_text())
    m["merged_from"] = [{"project": "hound_20", "name": "final.mp4"}, {"project": "hound_21", "name": "final.mp4"}]
    (p / "project.json").write_text(json.dumps(m))
    d = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d["action"] == "build" and not d.get("error"), d
    m2 = _wait(root, "hound_ch20-23")
    assert m2["build"]["status"] == "done"
    assert [x["project"] for x in m2["merged_from"]] == ["hound_20", "hound_21", "hound_22", "hound_23"]


def test_fl1_hold_keeps_chapters_as_singles(lib, monkeypatch):
    """FL-1 (owner, 2026-11-06): held series post new chapters as singles."""
    root, _ = lib
    monkeypatch.setenv("AUTO_APPEND_HOLD", "hound:22, bad, other:x")
    assert server._auto_append_holds() == {"hound": 22}
    d = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d["action"] == "none", d
    assert not os.path.isdir(os.path.join(root, "hound_ch20-22")) and not os.path.isdir(os.path.join(root, "hound_ch20-23"))
    monkeypatch.setenv("AUTO_APPEND_HOLD", "hound:23")             # chapters before the hold still join
    d = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d["action"] == "build" and (d["start"], d["end"]) == (20, 22), d
    assert _wait(root, "hound_ch20-22")["build"]["status"] == "done"
