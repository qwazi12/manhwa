"""One status for every chapter (rebuild step 2): the pure decision, the facts
gathered from real files, and the Home / Library / Chapter / chapters
endpoints the new frontend reads. Approving a video schedules it. Temp dirs."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []
URL = "https://asurascans.com/comics/murim-psychopath-6f7fe6eb/chapter/{}"


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import chapter_status as cs
    D = cs.decide
    check("ingest running -> Making", D({"ingest_status": "running"})["key"] == "making")
    check("budget pause -> Waiting", D({"ingest_status": "budget_paused"})["key"] == "waiting")
    check("ingest error -> Failed with the reason",
          D({"ingest_status": "error", "ingest_error": "scrape blocked"}) == cs.view("failed", reason="scrape blocked"))
    check("board built -> To review", D({"has_board": True, "ingest_status": "done"})["key"] == "to_review")
    check("render running -> Rendering", D({"has_board": True, "rendering": True})["key"] == "rendering")
    check("video, not approved -> Video ready", D({"has_board": True, "has_video": True})["key"] == "video_ready")
    check("cut changed after approval -> Video ready again",
          D({"has_video": True, "approved_video": True, "superseded": True})["key"] == "video_ready")
    check("queued -> Scheduled", D({"has_video": True, "scheduled": True})["key"] == "scheduled")
    check("uploading -> Posting", D({"has_video": True, "posting": True})["key"] == "posting")
    check("posted -> Posted", D({"has_video": True, "posted": True, "scheduled": True})["key"] == "posted")
    check("archived wins", D({"archived": {"x": 1}, "posted": True})["key"] == "archived")
    check("every status has a label, tone and plain hint",
          all(len(v) == 3 and v[2] for v in cs.STATUSES.values()))

    import ingest
    import server
    import watchlist
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="cs_")
    saved = (ingest.PROJECTS, dict(server.INGEST))
    ingest.PROJECTS = root
    server.INGEST.clear()
    watchlist.save(root, {"series": []})
    sid = watchlist.add_series(root, "Murim Psychopath", tier="greenlight")["id"]
    watchlist.add_mirror(root, sid, URL.format(44))

    def proj(ch, board=True, video=False):
        pid = ingest.project_id(URL.format(ch))
        p = os.path.join(root, pid)
        os.makedirs(os.path.join(p, "exports"), exist_ok=True)
        json.dump({"url": URL.format(ch), "series": "Murim Psychopath", "chapter": str(ch)},
                  open(os.path.join(p, "project.json"), "w"))
        if board:
            json.dump([], open(os.path.join(p, "segments.json"), "w"))
        if video:
            open(os.path.join(p, "exports", "final_a.mp4"), "wb").write(b"\0" * 10)
        return pid
    p44 = proj(44)
    p45 = proj(45, video=True)
    server.INGEST["j46"] = {"status": "running", "stage": "voice", "url": URL.format(46), "ts": 9e9}
    try:
        c = TestClient(server.app)
        d = c.get("/api/chapters").json()
        st = {r["id"]: r["status"]["key"] for r in d["chapters"]}
        check("the chapters list gives one status each", st[p44] == "to_review" and st[p45] == "video_ready")
        check("a chapter still being made (no folder yet) is listed as Making",
              any(r["status"]["key"] == "making" and r["chapter"] == "46" for r in d["chapters"]))
        check("counts per status", d["counts"].get("to_review") == 1 and d["counts"].get("video_ready") == 1)
        h = c.get("/api/home").json()
        kinds = [n["kind"] for n in h["need"]]
        check("Home lists what needs you (video to approve before board to review)",
              kinds[:2] == ["video_ready", "to_review"])
        check("Home has spend, autopilot, queue and disk", all(k in h for k in ("spend", "autopilot", "queue", "disk")))
        lib = c.get("/api/library").json()
        mur = next(x for x in lib["series"] if x["id"] == sid)
        check("Library puts each chapter under its series", {r["id"] for r in mur["chapters"]} >= {p44, p45})
        ch = c.get(f"/api/chapter/{p45}").json()
        check("the Chapter view has the step strip and the video's publish details",
              len(ch["steps"]) == 8 and ch["publish"]["url"].startswith("/export/final_a.mp4"))
        check("unknown chapter -> 404", c.get("/api/chapter/nope").status_code == 404)

        r = c.post("/api/review", json={"project": p45, "name": "final_a.mp4", "status": "approved", "notes": ""})
        check("approving a video schedules it (next free slot)", r.json()["scheduled"] is True)
        st = {x["id"]: x["status"]["key"] for x in c.get("/api/chapters").json()["chapters"]}
        check("...and its status is Scheduled everywhere", st[p45] == "scheduled")
        c.post("/api/review", json={"project": p45, "name": "final_a.mp4", "status": "sent_back", "notes": "fix"})
        st = {x["id"]: x["status"]["key"] for x in c.get("/api/chapters").json()["chapters"]}
        check("sending it back takes it off the queue", st[p45] == "video_ready")
    finally:
        ingest.PROJECTS = saved[0]
        server.INGEST.clear()
        server.INGEST.update(saved[1])


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
