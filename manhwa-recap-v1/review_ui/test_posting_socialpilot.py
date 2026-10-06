"""Posting schedule like Scrapper's SocialPilot (owner, 2026-10-06): every
queued video's planned post time; Mix & Shuffle by series with Undo; Post
next; bulk actions. Temp root, no network."""
import json
import os
import random
import sys
import tempfile
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


def at(day, hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 10, day, h, m, tzinfo=ET).timestamp()


def main():
    import publish_queue as pq
    root = tempfile.mkdtemp(prefix="sp_")
    pq.add(root, [{"project": p, "name": "v.mp4"} for p in ("a_1", "a_2", "b_1", "c_1", "a_3")])
    d = pq.load(root)
    ids = {x["project"]: x["id"] for x in d["items"]}
    sched = {"enabled": True, "times": ["11:00", "19:00"], "tz": "America/New_York", "per_channel_per_day": 2}
    plan = pq.planned(sched, d, at(6, 9, ) if False else at(6, "09:00"), lambda x: ["mk:youtube"])
    labels = [plan[ids[p]]["label"] for p in ("a_1", "a_2", "b_1", "c_1", "a_3")]
    check("each video gets its post time, in order, 2 a day", labels == [
        "Tue, Oct 6 at 11:00 AM ET", "Tue, Oct 6 at 7:00 PM ET", "Wed, Oct 7 at 11:00 AM ET", "Wed, Oct 7 at 7:00 PM ET", "Thu, Oct 8 at 11:00 AM ET"])
    plan2 = pq.planned({**sched, "per_channel_per_day": 1}, d, at(6, "12:00"), lambda x: ["mk:youtube"])
    check("an unfired time today posts at the next check; 1 a day spreads over days",
          plan2[ids["a_1"]]["label"] == "at the next check (11:00 slot)" and plan2[ids["a_2"]]["label"] == "Wed, Oct 7 at 11:00 AM ET")
    d3 = dict(d, slots_done={"2026-10-06": {"11:00": "posted"}})
    plan3 = pq.planned({**sched, "per_channel_per_day": 1}, d3, at(6, "12:00"), lambda x: ["mk:youtube"])
    check("a used time today is skipped", plan3[ids["a_1"]]["label"] == "Tue, Oct 6 at 7:00 PM ET")
    check("no channel picked: no time is planned", pq.planned(sched, d, at(6, "09:00"), lambda x: []) == {})
    check("schedule off: nothing planned", pq.planned({**sched, "enabled": False}, d, at(6, "09:00"), lambda x: ["y"]) == {})

    series = lambda x: x["project"].split("_")[0]
    before = [x["id"] for x in pq.load(root)["items"] if x["status"] == "queued"]
    pq.shuffle(root, "round_robin", series, random.Random(2))
    after = [x for x in pq.load(root)["items"] if x["status"] == "queued"]
    check("round-robin mix: no series twice in a row at the start", len({series(x) for x in after[:3]}) == 3)
    check("...a series keeps its chapter order", [x["project"] for x in after if series(x) == "a"] == ["a_1", "a_2", "a_3"])
    pq.undo_order(root)
    check("Undo restores the order", [x["id"] for x in pq.load(root)["items"] if x["status"] == "queued"] == before)
    pq.to_top(root, ids["c_1"])
    check("Post next puts it first", [x for x in pq.load(root)["items"] if x["status"] == "queued"][0]["id"] == ids["c_1"])

    import server
    import ingest
    saved = ingest.PROJECTS
    ingest.PROJECTS = tempfile.mkdtemp(prefix="spb_")
    try:
        from fastapi.testclient import TestClient
        c = TestClient(server.app)
        check("bulk: an unknown action is refused",
              c.post("/api/studio/bulk", json={"action": "nope", "items": [{"project": "x", "name": "y"}]}).status_code == 400)
        r = c.post("/api/studio/bulk", json={"action": "approve", "items": [{"project": "missing_1", "name": "v.mp4"}]}).json()
        check("bulk: a bad item is reported with its reason", r["failed"] and r["failed"][0]["reason"])
        check("shuffle refuses an unknown mode", c.post("/api/studio/queue/shuffle", json={"mode": "x"}).status_code == 400)
    finally:
        ingest.PROJECTS = saved


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
