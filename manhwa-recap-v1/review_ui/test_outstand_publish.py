"""Phase D tests — publishing through Outstand (Session 28).

Nothing here touches the live service: the Outstand module's network calls are
replaced. What is pinned is the behaviour that matters — a publish cannot start
unless every gate passes, privacyStatus is always explicit, and per-account
outcomes are kept separate rather than collapsed into one flag.

Run: python3 test_outstand_publish.py
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import ingest as ing
import outstand as osd
import server as srv


def _clear_env():
    for k in [k for k in os.environ if k.upper().startswith("OUTSTAND")]:
        os.environ.pop(k, None)


def _project(root, name="proj"):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    json.dump([{"seg_index": 0, "panel_id": "p", "dur": 3.0, "start": 0.0,
                "end": 3.0, "user_included": True, "crop_bbox_norm": None,
                "beats": [{"index": 0, "text": "x", "start": 0.0, "end": 3.0}]}],
              open(os.path.join(d, "segments.json"), "w"))
    json.dump({"series": "S", "chapter": "1"}, open(os.path.join(d, "project.json"), "w"))
    open(os.path.join(d, "exports", "final_a.mp4"), "wb").write(b"\0" * 2048)
    return d


def _err(fn, *a, **kw):
    try:
        fn(*a, **kw); return None
    except Exception as e:
        return getattr(e, "status_code", type(e).__name__)


def main():
    r = []
    root = tempfile.mkdtemp(prefix="pub_d_")
    ing.PROJECTS = root
    _clear_env()
    os.environ["OUTSTAND_API_KEY"] = "k"
    os.environ["OUTSTAND_ORG_ID"] = "o"
    os.environ["OUTSTAND_REDIRECT_URI"] = "https://x/cb"
    pd = _project(root)
    srv.active_project_dir = lambda: pd
    NAME = "final_a.mp4"

    # ---- the three-step media upload
    steps = []
    def http(method, url, body, cfg):
        steps.append(url)
        if url.endswith("/media/upload"):
            return {"id": "med_1", "upload_url": "https://up.test/put"}
        if url.endswith("/confirm"):
            return {"url": "https://media.outstand.so/org/med_1.mp4"}
        return {}
    puts = []
    def put(u, path, ctype, size):
        puts.append({"url": u, "ctype": ctype, "size": size}); return {"status": 200}
    med = osd.upload_media(os.path.join(pd, "exports", NAME), "video/mp4",
                           _http=http, _put=put)
    # Order is the contract; the exact index is not. The client probes both
    # documented spellings of the slot endpoint, so assert that a slot request
    # precedes the confirm and that exactly one PUT happens between them,
    # rather than pinning steps[0]/steps[1].
    slot = next((i for i, u in enumerate(steps) if u.endswith("/media/upload")), -1)
    conf = next((i for i, u in enumerate(steps) if u.endswith("/confirm")), -1)
    r.append(("upload asks for a slot, PUTs, then confirms — in that order",
              slot >= 0 and conf > slot and len(puts) == 1))
    r.append(("...PUTting to the signed URL Outstand returned",
              puts[0]["url"] == "https://up.test/put"))
    r.append(("...with the real byte size", puts[0]["size"] == 2048))
    r.append(("...and returning the public media URL",
              med["url"].endswith("med_1.mp4")))
    r.append(("a missing upload_url is an error, not a silent skip",
              _err(osd.upload_media, os.path.join(pd, "exports", NAME), "video/mp4",
                   None, lambda *a: {}, put) == "OutstandError"))
    r.append(("a missing file is refused",
              _err(osd.upload_media, os.path.join(pd, "nope.mp4")) == "OutstandError"))

    # ---- publish is refused until every gate passes
    osd.save_accounts(root, {"F473Z": {"account_id": "F473Z", "network": "youtube",
                                       "username": "flamingoremix", "active": True}})
    r.append(("publishing an unreviewed export is refused (409)",
              _err(srv.os_publish, srv.PublishNowIn(project="proj", name=NAME)) == 409))
    srv.api_review_save(srv.ReviewIn(project="proj", name=NAME, status="approved"))
    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME, metadata={
        "title": "Overgeared 338 Recap", "description": "A recap.",
        "tags": ["a"], "privacy": "private", "targets": ["F473Z"]}))
    r.append(("an approved, targeted, private export is eligible",
              srv.publish_eligibility(pd, NAME)["ready"] is True))

    # ---- run the job with the network replaced
    created_bodies = []
    def fake_upload(path, ctype="video/mp4", cfg=None, _http=None, _put=None,
                    on_step=None):
        if on_step: on_step("uploading")
        return {"media_id": "med_1", "url": "https://media.test/v.mp4",
                "filename": "final_a.mp4"}
    def fake_create(containers, accounts, scheduled_at=None, youtube=None,
                    cfg=None, _http=None):
        created_bodies.append({"containers": containers, "accounts": accounts,
                               "youtube": youtube, "scheduledAt": scheduled_at})
        return {"post_id": "9dyJS", "results": [
            {"account_id": "F473Z", "network": "youtube",
             "username": "flamingoremix", "status": "pending",
             "platform_post_id": None, "error": None, "url": None}]}
    def fake_status(post_id, cfg=None, _http=None):
        return {"post_id": post_id, "results": [
            {"account_id": "F473Z", "network": "youtube",
             "username": "flamingoremix", "status": "published",
             "platform_post_id": "yt_abc", "error": None,
             "url": "https://www.youtube.com/watch?v=yt_abc"}]}
    osd.upload_media, osd.create_post, osd.post_status = (
        fake_upload, fake_create, fake_status)
    _sleep = time.sleep
    time.sleep = lambda *_a: None

    jid = "job_test"
    srv.JOBS[jid] = {"status": "queued", "done": 0, "total": 4, "error": None,
                     "type": "publish", "control": "run"}
    srv._persist_job = lambda _j: None
    srv._run_publish_job(jid, pd, NAME)

    body = created_bodies[-1]
    r.append(("the post targets the selected account", body["accounts"] == ["F473Z"]))
    r.append(("...carries the video as media",
              body["containers"][0]["media"][0]["url"] == "https://media.test/v.mp4"))
    r.append(("...uses the description as post content",
              body["containers"][0]["content"] == "A recap."))
    r.append(("...sends privacyStatus EXPLICITLY as private",
              body["youtube"]["privacyStatus"] == "private"))
    r.append(("...maps the title to youtube.title",
              body["youtube"]["title"] == "Overgeared 338 Recap"))

    rec = (srv.load_publishes(pd) or {}).get(NAME) or {}
    r.append(("the publish record stores the post id", rec.get("post_id") == "9dyJS"))
    r.append(("...the per-account result", rec["results"][0]["status"] == "published"))
    r.append(("...the platform video id", rec["results"][0]["platform_post_id"] == "yt_abc"))
    r.append(("...a watch link", "youtube.com/watch" in rec["results"][0]["url"]))
    r.append(("...and an overall status of published", rec.get("status") == "published"))
    r.append(("the job finishes done", srv.JOBS[jid]["status"] == "done"))

    # ---- re-publishing the same export is blocked
    r.append(("re-publishing an already-published export is refused (409)",
              _err(srv.os_publish, srv.PublishNowIn(project="proj", name=NAME)) == 409))

    # ---- partial success must NOT read as published
    srv.save_publishes(pd, {})
    def mixed_status(post_id, cfg=None, _http=None):
        return {"post_id": post_id, "results": [
            {"account_id": "F473Z", "status": "published",
             "platform_post_id": "yt_1", "error": None, "network": "youtube"},
            {"account_id": "OTHER", "status": "failed", "platform_post_id": None,
             "error": "rate limited", "network": "x"}]}
    osd.post_status = mixed_status
    srv.JOBS[jid] = {"status": "queued", "done": 0, "total": 4, "error": None,
                     "type": "publish", "control": "run"}
    srv._run_publish_job(jid, pd, NAME)
    rec = (srv.load_publishes(pd) or {}).get(NAME) or {}
    r.append(("one success and one failure reports PARTIAL, not published",
              rec.get("status") == "partial"))
    r.append(("...keeping both outcomes separately", len(rec["results"]) == 2))

    # ---- an all-failed publish is failed
    srv.save_publishes(pd, {})
    osd.post_status = lambda pid, cfg=None, _http=None: {"post_id": pid, "results": [
        {"account_id": "F473Z", "status": "failed", "error": "quota",
         "platform_post_id": None, "network": "youtube"}]}
    srv.JOBS[jid] = {"status": "queued", "done": 0, "total": 4, "error": None,
                     "type": "publish", "control": "run"}
    srv._run_publish_job(jid, pd, NAME)
    rec = (srv.load_publishes(pd) or {}).get(NAME) or {}
    r.append(("an all-failed publish is recorded failed", rec.get("status") == "failed"))

    # ---- a stop request cancels mid-flight
    srv.save_publishes(pd, {})
    osd.post_status = fake_status
    srv.JOBS[jid] = {"status": "running", "done": 0, "total": 4, "error": None,
                     "type": "publish", "control": "stop"}
    srv._run_publish_job(jid, pd, NAME)
    rec = (srv.load_publishes(pd) or {}).get(NAME) or {}
    r.append(("a stop request cancels the publish", rec.get("status") == "cancelled"))
    r.append(("...and the job reports cancelled", srv.JOBS[jid]["status"] == "cancelled"))

    # ---- eligibility is re-checked at the moment of action
    srv.save_publishes(pd, {})
    json.dump([{"seg_index": 0, "panel_id": "CHANGED", "dur": 3.0, "start": 0.0,
                "end": 3.0, "user_included": True, "crop_bbox_norm": None,
                "beats": [{"index": 0, "text": "x", "start": 0.0, "end": 3.0}]}],
              open(os.path.join(pd, "segments.json"), "w"))
    srv.JOBS[jid] = {"status": "queued", "done": 0, "total": 4, "error": None,
                     "type": "publish", "control": "run"}
    srv._run_publish_job(jid, pd, NAME)
    rec = (srv.load_publishes(pd) or {}).get(NAME) or {}
    r.append(("a cut edited after queueing aborts the publish",
              rec.get("status") == "failed" and "no longer eligible" in (rec.get("error") or "")))

    time.sleep = _sleep
    _clear_env()
    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
