"""Logs redesign (owner, 2026-10-04: like Scrapper's): one live feed, jobs
with real names/costs/groups, a jobs bar on every page, spend in one place,
two-tap Stop. Temp projects root; no network."""
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


def main():
    import ingest
    import server
    import events
    import autopilot
    import usage
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="lg_")
    jobs_dir = os.path.join(root, "_jobs")
    os.makedirs(jobs_dir)
    saved = (ingest.PROJECTS, server._JOBS_DIR, usage.LOG_PATH)
    ingest.PROJECTS, server._JOBS_DIR = root, jobs_dir
    usage.LOG_PATH = os.path.join(root, "calls.log.jsonl")
    try:
        a = events.emit("ingest", "Murim Psychopath ch.44 → describe")
        b = events.emit("render", "boom", "error")
        check("events get increasing ids", b["id"] > a["id"])
        check("since(after) returns only newer lines", [e["msg"] for e in events.since(a["id"])] == ["boom"])
        check("kind filter works", [e["kind"] for e in events.since(0, kind="render")] == ["render"])
        events.MAX_LINES = 10
        for i in range(260):
            events.emit("x", f"n{i}")
        check("the feed trims itself (262 writes -> far fewer lines kept)", len(open(os.path.join(root, events.NAME)).readlines()) < 100)
        events.MAX_LINES = 5000

        check("chapter URL -> readable name",
              server._pretty("https://asurascans.com/comics/murim-psychopath-6f7fe6eb/chapter/44") == "Murim Psychopath ch.44")
        check("project id with a version -> readable name", server._pretty("murim-psychopath_44-img") == "Murim Psychopath ch.44 (img)")

        autopilot.audit("chapter_queued", series="Fog Land", chapter="3")
        last = events.since(0)[-1]
        check("autopilot decisions read in plain words", last["kind"] == "autopilot" and "picked Fog Land ch.3" in last["msg"])

        now = time.time()
        recs = {"j1": {"status": "done", "url": "https://asurascans.com/comics/murim-psychopath-6f7fe6eb/chapter/44",
                       "ts": now, "project": {"id": "murim-psychopath_44"}, "source": "autopilot"},
                "j2": {"status": "running", "url": "https://asurascans.com/comics/fog-land/chapter/3", "ts": now},
                "j3": {"status": "done", "url": "/export/final_Oct02.mp4", "ts": now - 86400 * 3},
                "j4": {"status": "budget_paused", "url": "https://asurascans.com/comics/x/chapter/1", "ts": now}}
        for k, v in recs.items():
            json.dump(v, open(os.path.join(jobs_dir, f"{k}.json"), "w"))
        json.dump({"type": "research", "status": "done", "project": "7 series", "ts": now, "done": 7, "total": 7},
                  open(os.path.join(jobs_dir, "render_r1.json"), "w"))
        open(usage.LOG_PATH, "w").write(json.dumps({"ts": "2026-10-04T12:00:00+00:00", "job_id": "j1",
                                                    "provider": "gemini", "est_cost_usd": 0.53}) + "\n")
        server._COST_CACHE["key"] = None
        c = TestClient(server.app)
        g = c.get("/api/logs/jobs").json()
        allr = {r["id"]: r for grp in g.values() for r in grp}
        check("running / waiting / finished groups", [r["id"] for r in g["running"]] == ["j2"]
              and [r["id"] for r in g["waiting"]] == ["j4"])
        check("an autopilot chapter is named and costed", allr["j1"]["kind"] == "autopilot"
              and allr["j1"]["name"] == "Murim Psychopath ch.44" and allr["j1"]["cost"] == 0.53)
        check("a deleted chapter is marked (deleted)", allr["j1"]["deleted"] is True)
        check("an export record is labelled export, not ingest", allr["j3"]["kind"] == "export")
        check("a research job is listed once, as research (not also as a blank ingest)",
              [r["kind"] for grp in g.values() for r in grp if r["id"] in ("r1", "render_r1")] == ["research"])
        sp = c.get("/api/spend").json()
        check("spend summary has today, autopilot budget and the month", "today" in sp and "autopilot" in sp
              and sp["month"]["label"])
        check("/api/events answers with a resume point", c.get("/api/events?after=0").json()["last"] > 0)
        check("/api/jobsbar answers", "jobs" in c.get("/api/jobsbar").json())
    finally:
        ingest.PROJECTS, server._JOBS_DIR, usage.LOG_PATH = saved
        server._COST_CACHE["key"] = None

    src = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    check("Logs has Live / Jobs / Spend / What changed tabs",
          all(f'id="lt_{t}"' in src for t in ("live", "jobs", "spend", "work")))
    th = open(os.path.join(HERE, "theme.py"), encoding="utf-8").read()
    check("the jobs bar (every page) uses the light feed, two-tap Stop and resume",
          "/api/jobsbar" in th and "tap again to stop" in th and "jobsResume" in th and 'id="jobsbar"' in src)
    check("Stop is two-tap (no confirm pop-up)", "function stopTwoTap(btn, id)" in src and "tap again to stop" in src)
    check("Logs opens on the live feed", "let logsWant = name === 'work' ? 'work' : 'live';" in src)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
