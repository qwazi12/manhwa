"""Owner, 2026-10-05: (1) the daily spend limit can be raised in Settings but
never past the Railway ceiling, and every paid call is still checked against
it; (2) a scheduled or failed video can go back to Needs you for review.
Temp dirs; no network, no money."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import usage
    import server
    import ingest
    import publish_queue as pq
    from fastapi.testclient import TestClient
    saved = (usage.USAGE_DIR, usage.LOG_PATH, usage.COUNTS_PATH, usage.LOCK_PATH,
             usage.MAX_DAILY_SPEND_USD, usage.MAX_DAILY_SPEND_CEILING_USD, ingest.PROJECTS)
    ud = tempfile.mkdtemp(prefix="cap_")
    usage.USAGE_DIR = ud
    usage.LOG_PATH, usage.COUNTS_PATH, usage.LOCK_PATH = (os.path.join(ud, "calls.log.jsonl"),
                                                          os.path.join(ud, "counters.json"), os.path.join(ud, ".lock"))
    usage.MAX_DAILY_SPEND_USD, usage.MAX_DAILY_SPEND_CEILING_USD = 10.0, 25.0
    root = tempfile.mkdtemp(prefix="qb_")
    ingest.PROJECTS = root
    try:
        c = TestClient(server.app)
        check("no setting: the Railway value applies", usage.daily_cap() == 10.0)
        r = c.post("/api/spend/cap", json={"usd": 15})
        check("raise to $15 in Settings", r.status_code == 200 and r.json()["cap"] == 15.0 and usage.daily_cap() == 15.0)
        check("above the Railway ceiling is refused",
              c.post("/api/spend/cap", json={"usd": 26}).status_code == 400 and usage.daily_cap() == 15.0)
        # the gate itself enforces the new number
        d = usage._load_counts()
        d["est_cost_usd"] = 14.99
        usage._save_counts(d)
        try:
            with usage.gate("claude", 1):
                pass
            check("a paid call over the new limit is still refused", False)
        except usage.UsageCapExceeded as e:
            check("a paid call over the new limit is still refused", "$15" in str(e))
        d = usage._load_counts(); d["est_cost_usd"] = 12.0; usage._save_counts(d)
        with usage.gate("claude", 1):
            pass
        check("...and allowed under it (it would have been refused at $10)", True)
        usage.MAX_DAILY_SPEND_CEILING_USD = 12.0
        check("lowering the Railway ceiling caps the Settings value", usage.daily_cap() == 12.0)
        usage.MAX_DAILY_SPEND_CEILING_USD = 25.0
        c.post("/api/spend/cap", json={"usd": None})
        check("Back to the Railway value", usage.daily_cap() == 10.0 and usage.cap_override() is None)
        ov = c.get("/api/settings/overview").json()["spending"]
        check("Settings shows the limit, default and ceiling", ov["cap"] == 10.0 and ov["ceiling"] == 25.0 and ov["railway_default"] == 10.0)

        # ---- back to review
        pid, name = "series-b_1", "final_x.mp4"
        pdir = os.path.join(root, pid)
        os.makedirs(os.path.join(pdir, "exports"))
        open(os.path.join(pdir, "exports", name), "wb").write(b"0" * 64)
        json.dump([], open(os.path.join(pdir, "segments.json"), "w"))
        json.dump({"id": pid, "url": "https://example.com/b/1"}, open(os.path.join(pdir, "project.json"), "w"))
        c.post("/api/review", json={"project": pid, "name": name, "status": "approved", "notes": "looks good"})
        q = [x for x in pq.load(root)["items"] if x["status"] == "queued"]
        check("approve puts it in the posting queue", len(q) == 1)
        r = c.post("/api/studio/queue/back", json={"project": pid, "name": name})
        check("back to review: off the queue", r.status_code == 200 and not [x for x in pq.load(root)["items"] if x["status"] == "queued"])
        rec = server.load_reviews(pdir)[name]
        check("...waiting for review again, notes kept, history recorded",
              rec["status"] == "review_pending" and rec["notes"] == "looks good" and rec["history"][-1]["status"] == "approved")
        row = next(x for x in server._chapter_rows() if x["id"] == pid)
        check("...and it shows as Watch the video (Needs you)", row["status"]["key"] == "video_ready")
        # failed rows too
        c.post("/api/review", json={"project": pid, "name": name, "status": "approved", "notes": ""})
        it = next(x for x in pq.load(root)["items"] if x["status"] == "queued")
        dd = pq.load(root)
        for x in dd["items"]:
            if x["id"] == it["id"]:
                x["status"] = "failed"
        pq._save(root, dd)
        c.post("/api/studio/queue/back", json={"project": pid, "name": name})
        check("a failed post can go back to review too", not [x for x in pq.load(root)["items"] if x["status"] in ("queued", "failed")])
    finally:
        (usage.USAGE_DIR, usage.LOG_PATH, usage.COUNTS_PATH, usage.LOCK_PATH,
         usage.MAX_DAILY_SPEND_USD, usage.MAX_DAILY_SPEND_CEILING_USD, ingest.PROJECTS) = saved


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
