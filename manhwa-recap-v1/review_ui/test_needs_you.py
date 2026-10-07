"""Owner, 2026-10-06: "remove videos from Needs you if it's on Posting schedule".
Run: python3 -m pytest test_needs_you.py -q"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)


def test_scheduled_or_posting_chapters_never_need_you(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    import chapter_status as cs
    ing.PROJECTS = str(tmp_path)
    rows = [{"id": "fated_358", "title": "Fated ch.358", "status": cs.view("to_review"), "url": ""},   # the ch.358 case
            {"id": "hound_183", "title": "Hound ch.183", "status": cs.view("video_ready"), "url": ""},
            {"id": "mount_180", "title": "Mount Hua ch.180", "status": cs.view("video_ready"), "url": ""},
            {"id": "murim_45", "title": "Murim ch.45", "status": cs.view("to_review"), "url": ""}]
    monkeypatch.setattr(srv, "_chapter_rows", lambda: rows)
    monkeypatch.setattr(srv._pq, "sync", lambda root, st: {"items": [
        {"project": "fated_358", "name": "a.mp4", "status": "queued"},
        {"project": "mount_180", "name": "b.mp4", "status": "posting"},
        {"project": "murim_45", "name": "old.mp4", "status": "posted"}]})
    need = {n.get("id") for n in srv.home_view()["need"] if n.get("id")}
    assert "fated_358" not in need and "mount_180" not in need       # on the Posting schedule
    assert {"hound_183", "murim_45"} <= need                          # not scheduled: still needs you
