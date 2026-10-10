"""Posting schedule (owner, 2026-10-04, step 5): built but OFF by default; when
on, each due time slot (Eastern) posts the next queued video through the
checked publish job, at most N posts per channel per local day, each slot once.
Temp projects root; the publish job is faked; no network."""
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []
ET = ZoneInfo("America/New_York")


def check(name, ok):
    R.append((name, bool(ok)))


def at(hm, day=5):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 10, day, h, m, tzinfo=ET).timestamp()


def _proj(root, pid, name):
    pdir = os.path.join(root, pid)
    os.makedirs(os.path.join(pdir, "exports"), exist_ok=True)
    json.dump({"series": pid, "chapter": "1"}, open(os.path.join(pdir, "project.json"), "w"))
    open(os.path.join(pdir, "exports", name), "wb").write(b"\0" * 100)
    json.dump({name: {"status": "approved"}}, open(os.path.join(pdir, "reviews.json"), "w"))
    # a publishable title (B1: hook first, unique per video)
    json.dump({name: {"title": f"He Wins The Duel {pid} - {pid} Chapter 1 Manhwa Recap", "targets": ["mk:youtube"]}},
              open(os.path.join(pdir, "publish.json"), "w"))


def main():
    import ingest
    import server
    import publish_queue as pq
    import studio_settings as ss
    from fastapi.testclient import TestClient

    # ---- pure decision
    S = {"enabled": True, "times": ["12:00", "18:00"], "tz": "America/New_York", "per_channel_per_day": 1}
    tg = lambda x: x.get("t", ["mk:youtube"])
    q = {"items": [{"id": "a", "status": "queued"}, {"id": "b", "status": "queued"}]}
    check("off by default", ss.DEFAULTS["schedule"]["enabled"] is False)
    check("off -> nothing happens", pq.decide(dict(S, enabled=False), q, at("12:05"), tg)["action"] == "off")
    check("before the first time -> wait", pq.decide(S, q, at("11:59"), tg)["action"] == "wait")
    d = pq.decide(S, q, at("12:05"), tg)
    check("at 12:05 the 12:00 slot posts the first queued video", d["action"] == "post" and d["item"]["id"] == "a" and d["slot"] == "12:00")
    q2 = dict(q, slots_done={"2026-10-05": {"12:00": "posted a"}})
    check("a used slot never fires twice", pq.decide(S, q2, at("13:00"), tg)["action"] == "wait")
    q3 = {"items": [{"id": "a", "status": "posted", "posted_day": "2026-10-05"}, {"id": "b", "status": "queued"}],
          "slots_done": {"2026-10-05": {"12:00": "posted a"}}}
    d = pq.decide(S, q3, at("18:01"), tg)
    check("the per-channel cap (1/day) skips the 18:00 slot", d["action"] == "skip" and "1 post" in d["why"])
    d = pq.decide(dict(S, per_channel_per_day=2), q3, at("18:01"), tg)
    check("...a cap of 2 lets it post", d["action"] == "post" and d["item"]["id"] == "b")
    q4 = {"items": [{"id": "a", "status": "posted", "posted_day": "2026-10-05", "t": ["mk:youtube"]},
                    {"id": "b", "status": "queued", "t": ["mk:youtube"]},
                    {"id": "c", "status": "queued", "t": ["default:youtube"]}]}
    d = pq.decide(S, q4, at("18:01"), tg)
    check("a video for a channel under its cap goes ahead of one over it", d["action"] == "post" and d["item"]["id"] == "c")
    check("yesterday's posts don't count today", pq.decide(S, q3, at("12:05", day=6), tg)["action"] == "post")
    check("empty queue -> the slot is skipped", pq.decide(S, {"items": []}, at("12:05"), tg)["action"] == "skip")
    check("next slot is reported", pq.next_slot(S, at("13:00")) == "Mon 18:00" and pq.next_slot(S, at("19:00")) == "Tue 12:00")

    # ---- the scheduler step, end to end
    root = tempfile.mkdtemp(prefix="sch_")
    saved = (ingest.PROJECTS, server.os_publish)
    ingest.PROJECTS = root
    posted = []

    def fake_publish(body):
        posted.append(body.project)
        return {"ok": True, "job": "j", "project": body.project, "name": body.name, "privacy": "public"}
    server.os_publish = fake_publish
    try:
        _proj(root, "p1", "a.mp4")
        _proj(root, "p2", "b.mp4")
        c = TestClient(server.app)
        c.post("/api/studio/queue", json={"items": [{"project": "p1", "name": "a.mp4"}, {"project": "p2", "name": "b.mp4"}]})
        check("schedule off: the scheduler posts nothing", server._schedule_post_pass(at("12:05")) is None and not posted)
        r = c.post("/api/settings", json={"schedule": {"times": ["25:00"]}})
        check("bad times are refused", r.status_code == 400)
        r = c.post("/api/settings", json={"schedule": {"enabled": True, "times": ["18:00", "12:00"], "per_channel_per_day": 1}})
        check("the schedule can be switched on", r.status_code == 200 and ss.load()["schedule"]["enabled"])
        # The switch-on time is stamped with the REAL clock; slots earlier that
        # day are skipped. Pin it to the test's own day so the test passes on
        # every date (it failed on 2026-10-05, the date it is set on).
        _st = json.load(open(ss._path()))
        _st["schedule"]["enabled_at"] = at("00:01")
        json.dump(_st, open(ss._path(), "w"))
        server._schedule_post_pass(at("12:05"))
        check("the due slot posts the first queued video", posted == ["p1"])
        server._schedule_post_pass(at("12:15"))
        check("...and only once (the next pass waits)", posted == ["p1"])
        # p1 is still 'posting' -> counts toward today's cap at 18:00
        server._schedule_post_pass(at("18:05"))
        check("the 18:00 slot respects 1 post per channel per day", posted == ["p1"])
        last = pq.load(root)["slots_done"]
        check("each slot's result is recorded", "posted p1" in last["2026-10-05"]["12:00"]
              and last["2026-10-05"]["18:00"].startswith("skipped"))

        def refuse(body):
            from fastapi import HTTPException
            raise HTTPException(409, "not ready to publish — no channel connected")
        server.os_publish = refuse
        server._schedule_post_pass(at("12:05", day=6))
        it = next(x for x in pq.load(root)["items"] if x["project"] == "p2")
        check("a refused post marks the video failed with the reason (nothing half-posted)",
              it["status"] == "failed" and "no channel" in it["error"])
        st = c.get("/api/studio").json()["schedule"]
        check("the studio shows the schedule state and next time", st["enabled"] and st["next"])

        # Owner, 2026-10-07: 11/3/9 slots, the first queued video couldn't post
        # and the whole slot was lost. Now the same slot goes to the next one.
        server.os_publish = fake_publish
        posted.clear()
        _proj(root, "p3", "c.mp4")
        _proj(root, "p4", "d.mp4")
        _proj(root, "p5", "e.mp4")
        # p3 has the same title as p4 (the Iron-Blooded ch.180 / ch.183 case)
        json.dump({"c.mp4": {"title": "He Wins The Duel p4 - p4 Chapter 1 Manhwa Recap", "targets": ["mk:youtube"]}},
                  open(os.path.join(root, "p3", "publish.json"), "w"))
        c.post("/api/studio/queue", json={"items": [{"project": p, "name": n} for p, n in
                                                    (("p3", "c.mp4"), ("p4", "d.mp4"), ("p5", "e.mp4"))]})
        st = c.get("/api/studio").json()
        b = {r["project"]: r for r in st["queue"]}
        check("a video that would be refused is flagged on the schedule before its slot (no planned time)",
              b["p3"].get("blocked") and "title" in b["p3"]["blocked"] and not b["p3"].get("planned"))
        server._schedule_post_pass(at("12:05", day=7))
        items = {x["project"]: x for x in pq.load(root)["items"]}
        # p3 and p4 share a title, so BOTH are refused (each duplicates the
        # other, like Iron-Blooded ch.180/183); p5 posts in the same 12:00 slot
        check("refused videos don't cost the slot: the next one that can post goes in the SAME slot",
              posted == ["p5"] and items["p3"]["status"] == items["p4"]["status"] == "failed")
        check("...and the slot's record says what was skipped",
              "after skipping" in pq.load(root)["slots_done"]["2026-10-07"]["12:00"])
        rows = {x["id"]: x["status"] for x in c.get("/api/chapters").json()["chapters"]}
        check("the refused video shows as needing you (couldn't post) with the reason",
              rows["p3"]["key"] == "failed" and "title" in rows["p3"]["reason"])

        # Sequential Chapter Posting Guard (e.g. Ch 25 cannot post before Ch 23)
        posted.clear()
        _proj(root, "solo-leveling_25", "sl25.mp4")
        _proj(root, "solo-leveling_23", "sl23.mp4")
        _proj(root, "solo-leveling_24", "sl24.mp4")
        c.post("/api/studio/queue", json={"items": [
            {"project": "solo-leveling_25", "name": "sl25.mp4"},
            {"project": "solo-leveling_24", "name": "sl24.mp4"},
            {"project": "solo-leveling_23", "name": "sl23.mp4"},
        ]})
        # Queue has 25 first, then 24, then 23.
        # When 18:00 slot runs, 25 and 24 are held back; 23 MUST post first!
        server._schedule_post_pass(at("12:05", day=8))
        check("sequential guard: earlier chapter 23 posts first even if 25 was queued first",
              posted == ["solo-leveling_23"])
    finally:
        ingest.PROJECTS, server.os_publish = saved


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
