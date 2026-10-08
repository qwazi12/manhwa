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


def test_needs_you_is_grouped_by_series_in_chapter_order(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    import chapter_status as cs
    ing.PROJECTS = str(tmp_path)
    mk = lambda pid, ser, ch: {"id": pid, "title": f"{ser} ch.{ch}", "series": ser, "chapter": ch,
                                "status": cs.view("video_ready"), "url": ""}
    monkeypatch.setattr(srv, "_chapter_rows", lambda: [
        mk("hound_93", "Hound", "93"), mk("mount_181", "Mount Hua", "181"), mk("hound_81", "Hound", "81"),
        mk("hound_100", "Hound", "100"), mk("mount_180", "Mount Hua", "180")])
    monkeypatch.setattr(srv._pq, "sync", lambda root, st: {"items": []})
    need = srv.home_view()["need"]
    order = [n["id"] for n in need if n.get("id") and n["kind"] != "hook"]
    assert order == ["hound_81", "hound_93", "hound_100", "mount_180", "mount_181"], order
    # spec 10: a series without a title hook gets ONE "pick the hook" item, first in its group
    kinds = [(n.get("series"), n["kind"]) for n in need if n.get("id")]
    assert kinds[0] == ("Hound", "hook") and kinds.count(("Hound", "hook")) == 1


def test_approving_out_of_order_still_posts_in_chapter_order(tmp_path):
    import publish_queue as pq
    root = str(tmp_path)
    pq.add(root, [{"project": "hound_93", "name": "a.mp4"}, {"project": "mount_180", "name": "b.mp4"}])
    pq.add(root, [{"project": "hound_81", "name": "c.mp4"}])        # approved after ch.93
    pq.add(root, [{"project": "hound_100", "name": "d.mp4"}])
    q = [x["project"] for x in pq.load(root)["items"] if x["status"] == "queued"]
    assert q == ["hound_81", "hound_93", "mount_180", "hound_100"], q
