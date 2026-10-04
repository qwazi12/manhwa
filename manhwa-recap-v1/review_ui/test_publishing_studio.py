"""📺 Publishing Studio (owner plan, 2026-10-04, step 4): lanes (needs review /
ready / queue / published), the posting queue, posting through the existing
checked publish job, and queued videos exempt from the 7-day cleanup.
Temp projects root; the publish job and YouTube are faked; no network."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def _proj(root, pid, series, ch, name, status=None, published=False, age_days=0):
    pdir = os.path.join(root, pid)
    os.makedirs(os.path.join(pdir, "exports"), exist_ok=True)
    json.dump({"series": series, "chapter": ch}, open(os.path.join(pdir, "project.json"), "w"))
    fp = os.path.join(pdir, "exports", name)
    open(fp, "wb").write(b"\0" * 1000)
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(fp, (t, t))
    if status:
        json.dump({name: {"status": status}}, open(os.path.join(pdir, "reviews.json"), "w"))
    if published:
        json.dump({name: {"status": "published", "ended_at": time.time(), "results": [
            {"account_id": "mk:youtube", "username": "Flamingo Remix", "status": "published",
             "url": "https://youtu.be/mUEMbhWHL6E", "published_at": time.time()}]}},
                  open(os.path.join(pdir, "publishes.json"), "w"))
    return pdir


def main():
    import ingest
    import server
    import publish_queue as pq
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="ps_")
    saved = (ingest.PROJECTS, server.os_publish, server.os_status)
    ingest.PROJECTS = root
    server.os_status = lambda: {"accounts": [{"account_id": "mk:youtube", "username": "Flamingo Remix", "active": True}]}
    _proj(root, "murim_44", "Murim Psychopath", "44", "final_a.mp4", status="approved")
    _proj(root, "fog_2", "Fog Land", "2", "final_b.mp4")
    _proj(root, "old_1", "Old Series", "1", "final_c.mp4", status="approved", published=True)
    posted = []

    def fake_publish(body):
        posted.append((body.project, body.name))
        return {"ok": True, "job": "job123", "project": body.project, "name": body.name, "privacy": "private"}
    server.os_publish = fake_publish
    try:
        c = TestClient(server.app)
        d = c.get("/api/studio").json()
        check("an approved, unposted export is Ready to post", [r["project"] for r in d["ready"]] == ["murim_44"])
        check("an unreviewed export is in Needs review", [r["project"] for r in d["review"]] == ["fog_2"])
        check("a posted export is in Published with its link and video id",
              d["published"] and d["published"][0]["posts"][0]["video_id"] == "mUEMbhWHL6E")
        check("ready rows carry the publish defaults (Flamingo Remix, private)",
              d["ready"][0]["targets"] == ["mk:youtube"] and d["ready"][0]["privacy"] == "private")
        check("the schedule is reported off", d["schedule"]["enabled"] is False)

        r = c.post("/api/studio/queue", json={"items": [{"project": "fog_2", "name": "final_b.mp4"}]})
        check("an unapproved export cannot be queued", r.status_code == 409)
        r = c.post("/api/studio/queue", json={"items": [{"project": "murim_44", "name": "final_a.mp4"}],
                                              "targets": ["mk:youtube"], "privacy": "unlisted"})
        check("queueing an approved export works", r.status_code == 200 and len(r.json()["added"]) == 1)
        pub = json.load(open(os.path.join(root, "murim_44", "publish.json")))
        check("channels and privacy picked once are saved on the video", pub["final_a.mp4"]["privacy"] == "unlisted")
        r = c.post("/api/studio/queue", json={"items": [{"project": "murim_44", "name": "final_a.mp4"}]})
        check("queueing twice is a no-op", r.json()["added"] == [] and len(r.json()["skipped"]) == 1)
        check("bad privacy is refused", c.post("/api/studio/queue", json={
            "items": [{"project": "murim_44", "name": "final_a.mp4"}], "privacy": "friends"}).status_code == 400)
        d = c.get("/api/studio").json()
        check("a queued video leaves Ready and shows in the Queue",
              not d["ready"] and [r["project"] for r in d["queue"]] == ["murim_44"])

        # retention exemption
        pdir = _proj(root, "stale_9", "Stale", "9", "final_s.mp4", status="approved", age_days=10)
        os.utime(os.path.join(root, "murim_44", "exports", "final_a.mp4"), (time.time() - 10 * 86400,) * 2)
        server.prune_exports()
        check("a queued video older than 7 days is kept", os.path.exists(os.path.join(root, "murim_44", "exports", "final_a.mp4")))
        check("...an unqueued old one is still cleaned up", not os.path.exists(os.path.join(pdir, "exports", "final_s.mp4")))
        os.utime(os.path.join(root, "murim_44", "exports", "final_a.mp4"), None)

        # reorder / remove
        _proj(root, "iron_183", "Iron-Blooded", "183", "final_i.mp4", status="approved")
        c.post("/api/studio/queue", json={"items": [{"project": "iron_183", "name": "final_i.mp4"}]})
        q = [x["id"] for x in pq.load(root)["items"] if x["status"] == "queued"]
        c.post("/api/studio/queue/reorder", json={"ids": [q[1], q[0]]})
        q2 = [x["project"] for x in pq.load(root)["items"] if x["status"] == "queued"]
        check("the queue can be reordered", q2 == ["iron_183", "murim_44"])
        r = c.post("/api/studio/queue/remove", json={"id": q[1]})
        check("a queued video can be removed (kept as a record)",
              r.status_code == 200 and pq.load(root)["items"][0]["status"] == "removed")
        check("removing an unknown item -> 404", c.post("/api/studio/queue/remove", json={"id": "nope"}).status_code == 404)

        # post now -> existing publish job; settled from the publish record
        r = c.post("/api/studio/queue/post", json={"id": q[0]})
        check("Post now runs the existing checked publish job",
              r.status_code == 200 and posted == [("murim_44", "final_a.mp4")])
        check("...and the item is marked posting", next(x for x in pq.load(root)["items"] if x["id"] == q[0])["status"] == "posting")
        check("a posting item cannot be posted twice", c.post("/api/studio/queue/post", json={"id": q[0]}).status_code == 409)
        check("a posting item cannot be removed", c.post("/api/studio/queue/remove", json={"id": q[0]}).status_code == 409)
        json.dump({"final_a.mp4": {"status": "published", "results": [
            {"account_id": "mk:youtube", "status": "published", "url": "https://www.youtube.com/watch?v=abcdefghijk"}]}},
                  open(os.path.join(root, "murim_44", "publishes.json"), "w"))
        d = c.get("/api/studio").json()
        it = next(x for x in pq.load(root)["items"] if x["id"] == q[0])
        check("once the platform confirms, the item is posted and off the queue",
              it["status"] == "posted" and not [r for r in d["queue"] if r["project"] == "murim_44"])
        check("...and it appears in Published", any(p["project"] == "murim_44" for p in d["published"]))
        check("a posted video is no longer protected from cleanup", ("murim_44", "final_a.mp4") not in pq.protected(root))
        check("a posted video cannot be queued again", c.post("/api/studio/queue", json={
            "items": [{"project": "murim_44", "name": "final_a.mp4"}]}).json()["added"] == [])

        # failed post goes back to the queue lane with its error; retry allowed
        _proj(root, "ext_5", "Extra", "5", "final_e.mp4", status="approved")
        c.post("/api/studio/queue", json={"items": [{"project": "ext_5", "name": "final_e.mp4"}]})
        qid = next(x["id"] for x in pq.load(root)["items"] if x["project"] == "ext_5")
        c.post("/api/studio/queue/post", json={"id": qid})
        json.dump({"final_e.mp4": {"status": "failed", "error": "quota exceeded", "results": []}},
                  open(os.path.join(root, "ext_5", "publishes.json"), "w"))
        d = c.get("/api/studio").json()
        row = next(r for r in d["queue"] if r["project"] == "ext_5")
        check("a failed post stays visible with its error", row["qstatus"] == "failed" and "quota" in row["qerror"])
        check("...and can be tried again", c.post("/api/studio/queue/post", json={"id": qid}).status_code == 200)
    finally:
        ingest.PROJECTS, server.os_publish, server.os_status = saved

    sb = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    th = open(os.path.join(HERE, "theme.py"), encoding="utf-8").read()
    check("Publishing Studio is a panel of the main app (nav, view, drawer, loader)",
          'd="publish"' in th and "'publish'" in sb and 'id="d_publish"' in sb
          and "if (name === 'publish') loadStudio();" in sb)
    check("posting needs two taps", "tap again to post" in sb)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
