"""Owner, 2026-10-07: 'ensure the accuracy of what's made and what the tracker is
tracking'. A chapter made then removed (deleted / cleaned up after posting) is
shown as made — never 'Not made' with a paid Make button.
Run: python3 -m pytest test_library_truth.py -q"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)
URL = "https://asurascans.com/comics/a-regressors-tale-of-cultivation-3ec3b16f/chapter/{}"


def test_removed_chapters_stay_made_with_the_reason(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    import chapter_status as cs
    import watchlist as wl
    ing.PROJECTS = str(tmp_path)
    # ch.30 was posted, then its folder was deleted: the removal is recorded
    d30 = tmp_path / "a-regressors-tale-of-cultivation_30"
    d30.mkdir()
    (d30 / "project.json").write_text(json.dumps({"series": "RTOC", "chapter": "30", "url": URL.format(30)}))
    (d30 / "publishes.json").write_text(json.dumps({"f.mp4": {"results": [
        {"status": "published", "network": "youtube", "url": "https://www.youtube.com/watch?v=abcdefghijk"}]}}))
    monkeypatch.setattr(srv, "_chapter_busy", lambda p: None)
    monkeypatch.setattr(srv, "active_project_dir", lambda: str(tmp_path / "other"))
    ok, why, _mb = srv._delete_one_project("a-regressors-tale-of-cultivation_30", why="cleaned up after posting (archive)")
    assert ok and not d30.exists()
    gone = json.loads((tmp_path / "_gone.json").read_text())
    assert gone["a-regressors-tale-of-cultivation_30"]["posted"].startswith("https://")
    # the Library: series made 29 (no record), 30 (recorded), 31 (folder still here)
    sid = "rtoc"
    monkeypatch.setattr(srv, "series_board", lambda: {"autopilot": {}, "series": [
        {"id": sid, "title": "A Regressor's Tale", "made": ["29", "30", "31"]}]})
    monkeypatch.setattr(srv, "_chapter_rows", lambda: [
        {"id": "a-regressors-tale-of-cultivation_31", "chapter": "31", "url": URL.format(31),
         "status": cs.view("video_ready")}])
    monkeypatch.setattr(wl, "find_by_mirror", lambda data, url: ({"id": sid}, None) if "cultivation" in url else (None, None))
    lib = srv.library_view()["series"][0]
    rows = {str(r["chapter"]): r for r in lib["chapters"]}
    assert set(rows) == {"29", "30", "31"}                         # count == cards
    assert rows["30"]["gone"] and rows["30"]["status"]["label"] == "Made · folder removed"
    assert "posted, then cleaned up after posting" in rows["30"]["status"]["reason"]
    assert rows["30"]["posted_url"].startswith("https://www.youtube.com/")
    assert "before removals were recorded" in rows["29"]["status"]["reason"]
    assert rows["31"].get("id") == "a-regressors-tale-of-cultivation_31"
