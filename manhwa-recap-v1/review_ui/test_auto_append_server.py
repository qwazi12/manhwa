"""Auto-append end to end on a temp library (owner rule 2026-11-08, no overlaps):
posted 20-21 + rendered 22, 23 → 22-23 in Needs review with the posted video's
title hook / tags / thumbnail art; then 24 finishes → 22-24 replaces the waiting 22-23."""
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
    monkeypatch.setenv("AUTO_APPEND_MODE", "fresh")            # these tests pin the 2026-11-08 rule
    monkeypatch.setenv("AUTO_APPEND_MIN", "2")
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


def test_next_merge_starts_after_the_posted_video_with_its_seo(lib):
    root, tmp_path = lib
    plans = server._auto_append_pass()
    d = next(x for x in plans if x["series"] == "hound")
    assert d["action"] == "build" and (d["start"], d["end"]) == (22, 23), d
    m = _wait(root, "hound_ch22-23")
    assert m["build"]["status"] == "done", m
    assert [x["project"] for x in m["merged_from"]] == ["hound_22", "hound_23"]      # no posted chapters inside
    md = server.load_publish(os.path.join(root, "hound_ch22-23"))["final_range_22-23.mp4"]
    assert md["title"] == "[22-23] Hound Returns For VENGEANCE | Flamingo Recap"
    tags = [x.lower() for x in md["tags"]]
    assert "hound chapter 22-23" in tags and "hound chapters 22 to 23" in tags
    assert "hound chapter 3" not in tags and len(tags) == len(set(tags))
    assert "• 0:00 - Chapter 22\n• 10:00 - Chapter 23" in md["description"]
    thumb = tb.path_for(os.path.join(root, "hound_ch22-23"), "final_range_22-23.mp4")
    im = Image.open(thumb).convert("RGB")
    r, g, b = im.getpixel((40, 45))
    assert r > 200 and 140 < g < 220 and b < 120        # badge kept the posted colour
    assert server.load_reviews(os.path.join(root, "hound_22"))["final.mp4"]["merged_into"] == "hound_ch22-23"
    again = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert again["action"] == "none"

    # chapter 24 finishes: 22-24 replaces the waiting 22-23, which is moved aside
    _chapter(tmp_path, 24)
    d2 = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d2["action"] == "build" and (d2["start"], d2["end"]) == (22, 24) and d2["retire"] == ["hound_ch22-23"]
    m2 = _wait(root, "hound_ch22-24")
    assert m2["build"]["status"] == "done"
    for _ in range(100):
        if not os.path.isdir(os.path.join(root, "hound_ch22-23")):
            break
        time.sleep(0.05)
    assert not os.path.isdir(os.path.join(root, "hound_ch22-23"))
    assert not any(x.startswith("_unmerged_") for x in os.listdir(root))          # nothing hidden fills the disk
    assert any(x.startswith("hound_ch22-23__") for x in os.listdir(os.path.join(root, server.TRASH_DIR)))
    assert any(i["name"].startswith("hound_ch22-23__") for i in server.api_trash()["items"])
    for n in (22, 23, 24):
        assert server.load_reviews(os.path.join(root, f"hound_{n}"))["final.mp4"]["merged_into"] == "hound_ch22-24"


def test_waits_for_auto_append_min(lib, monkeypatch):
    root, _ = lib
    monkeypatch.setenv("AUTO_APPEND_MIN", "7")
    assert server._auto_append_min() == 7
    d = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d["action"] == "wait" and "starts at 22 once 7" in d["why"], d
    assert not os.path.isdir(os.path.join(root, "hound_ch22-23"))
    monkeypatch.setenv("AUTO_APPEND_MIN", "x")
    assert server._auto_append_min() == 7
    monkeypatch.delenv("AUTO_APPEND_MIN")
    assert server._auto_append_min() == 7


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


def test_posted_merge_whose_file_is_gone_does_not_matter(lib, tmp_path):
    root, tmp_path = lib
    p = tmp_path / "hound_ch20-21"
    os.remove(p / "exports" / "final_range_20-21.mp4")          # cleared before Drive archiving
    d = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d["action"] == "build" and not d.get("error"), d
    m2 = _wait(root, "hound_ch22-23")
    assert m2["build"]["status"] == "done"
    assert [x["project"] for x in m2["merged_from"]] == ["hound_22", "hound_23"]


def test_fl1_hold_keeps_chapters_as_singles(lib, monkeypatch):
    """FL-1 (owner, 2026-11-06): held series post new chapters as singles."""
    root, _ = lib
    monkeypatch.setenv("AUTO_APPEND_HOLD", "hound:22, bad, other:x")
    assert server._auto_append_holds() == {"hound": 22}
    d = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d["action"] == "none", d
    assert not os.path.isdir(os.path.join(root, "hound_ch22-22")) and not os.path.isdir(os.path.join(root, "hound_ch22-23"))
    monkeypatch.setenv("AUTO_APPEND_HOLD", "hound:23")             # chapters before the hold still merge
    monkeypatch.setenv("AUTO_APPEND_MIN", "1")
    d = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d["action"] == "build" and (d["start"], d["end"]) == (22, 22), d
    assert _wait(root, "hound_ch22-22")["build"]["status"] == "done"


def test_growth_snapshot_is_read_only(lib):
    """F-02 exports: series/queue/posted from files only; nothing changes on disk."""
    from fastapi.testclient import TestClient
    root, _ = lib
    def tree():
        return sorted((dp, f, os.path.getmtime(os.path.join(dp, f))) for dp, _d, fs in os.walk(root) for f in fs)
    before = tree()
    os.environ.pop("SHARED_SECRET", None)
    d = TestClient(server.app).get("/api/growth/snapshot?days=100000").json()
    assert tree() == before
    assert d["read_only"] is True and isinstance(d["queue"], list)
    p = next(x for x in d["posted"] if x["project"] == "hound_ch20-21")
    assert p["video_id"] == "abcdefghijk" and p["kind"] == "merge" and p["range"] == "20-21"
    assert p["title"].startswith("[20-21] Hound")


def test_playlist_link_needs_a_real_playlist_id(lib, monkeypatch):
    """2026-11-06: a series NAME stored as playlist_id made 'list=Hound' links."""
    root, _ = lib
    pdir = os.path.join(root, "hound_22")
    for pl, want in (("Hound", False), ("PLabcdefghijkl123", True)):
        monkeypatch.setattr(server, "_series_pack", lambda *a, **k: {"playlist_id": pl, "title_en": "Hound"})
        text, _s = server._description_from_blocks(pdir, {"title": "[22] Hook | Flamingo Recap"},
                                                   {"summary": "A thing happens."})
        assert ("Full series playlist" in text) is want, text
        assert "list=Hound" not in text


# ---- owner brief 2026-11-13: each new chapter extends the previous compilation

def test_cumulative_is_the_default_and_one_new_chapter_extends_the_posted_video(lib, monkeypatch):
    root, tmp_path = lib
    monkeypatch.delenv("AUTO_APPEND_MODE")
    monkeypatch.delenv("AUTO_APPEND_MIN")
    assert server._auto_append_mode() == "cumulative" and server._auto_append_min() == 1
    os.rename(os.path.join(root, "hound_23"), os.path.join(root, "_later_23"))      # only chapter 22 is new
    d = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d["action"] == "build" and (d["start"], d["end"]) == (20, 22), d
    m = _wait(root, "hound_ch20-22")
    assert m["build"]["status"] == "done", m
    # the posted compilation is the first piece — re-used, not rebuilt from chapters
    assert [x["project"] for x in m["merged_from"]] == ["hound_ch20-21", "hound_22"]
    assert [int(n) for n, _t in m["build"]["marks"]] == [20, 21, 22]               # its own markers carried in
    md = server.load_publish(os.path.join(root, "hound_ch20-22"))["final_range_20-22.mp4"]
    assert md["title"] == "[20-22] Hound Returns For VENGEANCE | Flamingo Recap"
    # every chapter's check results were stored with the new compilation
    # each piece's file hash and check results were stored for THIS export
    ev = json.load(open(os.path.join(root, "hound_ch20-22", "release_evidence.json")))["exports"]["final_range_20-22.mp4"]
    assert [(e["project"], e["name"], e["kind"]) for e in ev["components"]] == [
        ("hound_ch20-21", "final_range_20-21.mp4", "range"), ("hound_22", "final.mp4", "chapter")]
    assert all(e["export_sha256"] for e in ev["components"])
    assert next(x for x in server._auto_append_pass() if x["series"] == "hound")["action"] == "none"

    # chapter 23 arrives while 20-22 waits for review: 20-23 replaces it
    os.rename(os.path.join(root, "_later_23"), os.path.join(root, "hound_23"))
    d2 = next(x for x in server._auto_append_pass() if x["series"] == "hound")
    assert d2["action"] == "build" and (d2["start"], d2["end"]) == (20, 23) and d2["retire"] == ["hound_ch20-22"]
    m2 = _wait(root, "hound_ch20-23")
    assert [x["project"] for x in m2["merged_from"]] == ["hound_ch20-21", "hound_22", "hound_23"]


def test_unknown_mode_is_refused(lib, monkeypatch):
    monkeypatch.setenv("AUTO_APPEND_MODE", "sometimes")
    with pytest.raises(RuntimeError):
        server._auto_append_mode()


def test_dry_run_reports_the_mode(lib, monkeypatch):
    monkeypatch.setenv("AUTO_APPEND_MODE", "cumulative")
    r = server.api_auto_append()
    assert r["mode"] == "cumulative" and r["min_new"] == 2


def test_new_merge_gets_the_series_search_pack(lib):
    """Owner 2026-11-14: search name in the title, locked hook, other names, three hashtags."""
    root, tmp_path = lib
    server.api_series_search_pack_save(server.SearchPackIn(records=[{
        "series_id": "hound", "search_title": "Revenge of the Iron-Blooded Sword Hound",
        "hook": "Disposable Hound Returns For VENGEANCE", "aka_names": ["Revenge of the Baskerville Bloodhound"],
        "characters": ["Vikir"], "hashtags": ["#IronBloodedSwordHound", "#ManhwaRecap", "#Regression"]}]))
    server._auto_append_pass()
    assert _wait(root, "hound_ch22-23")["build"]["status"] == "done"
    md = server.load_publish(os.path.join(root, "hound_ch22-23"))["final_range_22-23.mp4"]
    assert md["title"] == "[22-23] Disposable Hound Returns For VENGEANCE | Revenge of the Iron-Blooded Sword Hound"
    d = md["description"]
    assert "\U0001F4D6 Series: Revenge of the Iron-Blooded Sword Hound\nAlso known as: Revenge of the Baskerville Bloodhound\nMain characters: Vikir" in d
    assert d.rstrip().split("\n")[-1] == "#IronBloodedSwordHound #ManhwaRecap #Regression"
    assert "• 0:00 - Chapter 22\n• 10:00 - Chapter 23" in d                      # the timestamps are kept
    assert md["hashtags"] == ["#IronBloodedSwordHound", "#ManhwaRecap", "#Regression"]
    posted = server.load_publish(os.path.join(root, "hound_ch20-21"))["final_range_20-21.mp4"]
    assert posted["title"] == "[20-21] Hound Returns For VENGEANCE | Flamingo Recap"        # the posted video is not touched
