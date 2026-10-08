"""Owner, 2026-10-07: errors go back in line (like Scrapper), mass edit,
privacy without opening the chapter, a re-render clears the old error, a
finished upload stops saying 'uploading now…'. Run: python3 -m pytest test_queue_tools.py -q"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)


def _proj(root, pid, name, title):
    d = os.path.join(root, pid)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    json.dump({"series": pid, "chapter": "1"}, open(os.path.join(d, "project.json"), "w"))
    open(os.path.join(d, "exports", name), "wb").write(b"\0" * 10)
    json.dump({name: {"status": "approved"}}, open(os.path.join(d, "reviews.json"), "w"))
    json.dump({name: {"title": title, "targets": ["mk:youtube"], "privacy": "public"}},
              open(os.path.join(d, "publish.json"), "w"))
    return d


def test_back_in_line_mass_edit_privacy_and_stale_errors(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    import publish_queue as pq
    ing.PROJECTS = str(tmp_path)
    da = _proj(str(tmp_path), "pa", "a.mp4", "He Wins - pa Chapter 1 Manhwa Recap")
    db = _proj(str(tmp_path), "pb", "b.mp4", "She Wins - pb Chapter 1 Manhwa Recap")
    pq.add(str(tmp_path), [{"project": "pa", "name": "a.mp4"}, {"project": "pb", "name": "b.mp4"}])
    items = {x["project"]: x for x in pq.load(str(tmp_path))["items"]}
    pq.mark(str(tmp_path), items["pa"]["id"], status="failed", error="Upload-Post said no")
    B = lambda action, its, **kw: srv.studio_bulk(srv.StudioBulkIn(action=action, items=its, **kw))
    # ↩ back in line (Scrapper's change_status -> ready)
    r = B("requeue", [{"project": "pa", "name": "a.mp4", "qid": items["pa"]["id"]}])
    it = next(x for x in pq.load(str(tmp_path))["items"] if x["project"] == "pa")
    assert r["done"] and it["status"] == "queued" and it["error"] is None
    # a video that isn't failed can't be "requeued"
    r = B("requeue", [{"project": "pb", "name": "b.mp4", "qid": items["pb"]["id"]}])
    assert r["failed"]
    # mass edit: only given fields change
    both = [{"project": "pa", "name": "a.mp4"}, {"project": "pb", "name": "b.mp4"}]
    B("edit", both, privacy="unlisted", tags="rtoc, cultivation manhwa")
    for d, n, t in ((da, "a.mp4", "He Wins"), (db, "b.mp4", "She Wins")):
        md = json.load(open(os.path.join(d, "publish.json")))[n]
        assert md["privacy"] == "unlisted" and md["tags"] == ["rtoc", "cultivation manhwa"]
        assert md["title"].startswith(t)                 # untouched: not given
    import pytest
    with pytest.raises(Exception):
        srv.studio_bulk(srv.StudioBulkIn(action="edit", items=both, privacy="secret"))
    # a failed post whose chapter was re-rendered is closed (not in Errors AND Needs review)
    pq.mark(str(tmp_path), items["pb"]["id"], status="failed", error="cut changed")
    time.sleep(0.01)
    open(os.path.join(db, "exports", "b2.mp4"), "wb").write(b"\0" * 10)      # the new render
    os.utime(os.path.join(db, "exports", "b2.mp4"), (time.time() + 5, time.time() + 5))
    srv._close_stale_failures()
    it = next(x for x in pq.load(str(tmp_path))["items"] if x["project"] == "pb")
    assert it["status"] == "removed" and "replaced by a new render" in it["error"]
    errs = [r for r in srv.studio_overview()["queue"] if r.get("qstatus") == "failed"]
    assert not any(r["project"] == "pb" for r in errs)


def test_a_finished_upload_is_reconciled(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    import publish_queue as pq
    ing.PROJECTS = str(tmp_path)
    _proj(str(tmp_path), "pm", "m.mp4", "He Wins - pm Chapter 1 Manhwa Recap")
    pq.add(str(tmp_path), [{"project": "pm", "name": "m.mp4"}])
    qid = pq.load(str(tmp_path))["items"][0]["id"]
    pq.mark(str(tmp_path), qid, status="posting")
    asked = []

    def fake_status(project, name):
        asked.append(project)                    # Upload-Post answers: it went out
        srv._publish_record(srv.project_dir_for(project), name, status="published",
                            results=[{"status": "published", "network": "youtube", "url": "https://youtu.be/x"}])
        return {}
    monkeypatch.setattr(srv, "os_publish_status", fake_status)
    assert srv._reconcile_posting() == 1 and asked == ["pm"]
    assert pq.load(str(tmp_path))["items"][0]["status"] == "posted"
