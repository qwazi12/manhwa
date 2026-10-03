"""Chapter Autopilot: what it picks, when it waits, and that stop / resume /
never-repeat / archive behave as the owner asked (2026-10-03). Temporary
projects root, fake watchlist and queue; no network, no money."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def series(sid, rank, chapters, ingested=(), tier="greenlight", key=None):
    key = key or f"webtoon:{rank}"
    return {"id": sid, "title": sid.title(), "tier": tier, "rank": rank,
            "best_mirror": key, "ingested": list(ingested),
            "mirrors": [{"series_key": key, "source": "webtoon", "support": "supported",
                         "status": "ok", "series_url": f"https://www.webtoons.com/x?title_no={rank}",
                         "chapters": [str(c) for c in chapters]}]}


class Fake:
    """The live pieces tick() needs, in memory."""

    def __init__(self, view):
        self.view_rows = view
        self.queued = []
        self.busy = False
        self.spent, self.cap = 0.0, 6.0

    def deps(self):
        return {"view": lambda: self.view_rows, "refresh": lambda: None,
                "canon": lambda k: k, "live_projects": lambda: set(),
                "queue_busy": lambda: self.busy, "spend": lambda: (self.spent, self.cap),
                "chapter_url": lambda row, ch: f"https://src/{row['series_id']}/{ch}",
                "project_id": lambda url: url.rsplit("/", 2)[-2] + "_" + url.rsplit("/", 1)[-1],
                "enqueue": self.enqueue}

    def enqueue(self, url, engine):
        self.queued.append((url, engine))
        return f"job{len(self.queued)}"


def finish_all(ap, root, fake, status="done", cost=0.3):
    for i in range(len(fake.queued)):
        ap.on_job_end(root, f"job{i + 1}", status, None, cost)


def main():
    import autopilot as ap
    root = tempfile.mkdtemp(prefix="ap_")
    view = [series("alpha", 1, range(1, 11)),            # latest 3: 8 9 10
            series("bravo", 2, range(1, 6), ingested=["4"]),   # latest 3: 3 4 5, 4 made
            series("charlie", 3, [1, 2])]                # fewer than 3: starts at 1
    fake = Fake(view)
    now = time.time()

    check("off by default: nothing starts", ap.tick(root, fake.deps(), now) is None
          and fake.queued == [])
    ap.update_settings(root, {"enabled": True})
    s = ap.settings(root)
    check("defaults: 4 chapters a day, Gemini", s["per_day"] == 4 and s["engine"] == "gemini")

    picks = []
    for _ in range(4):
        r = ap.tick(root, fake.deps(), now)
        picks.append((r["series"], r["chapter"]) if r else None)
    check("round robin in rank order, starting at each series' latest 3 (made ch.4 skipped)",
          picks == [("Alpha", "8"), ("Bravo", "3"), ("Charlie", "1"), ("Alpha", "9")])
    check("every pick ran on Gemini", all(e == "gemini" for _, e in fake.queued))
    check("5th chapter waits: today's limit of 4",
          ap.tick(root, fake.deps(), now) is None and "limit" in ap.runtime["last_result"])

    st = ap.load(root)
    check("window is fixed when first seen", st["start_from"] == {"alpha": "8", "bravo": "3", "charlie": "1"})

    # next day: carries on, in order
    tomorrow = now + 86400
    finish_all(ap, root, fake)
    r = ap.tick(root, fake.deps(), tomorrow)
    check("next day continues round robin (fewest made first)", r and (r["series"], r["chapter"]) == ("Bravo", "5"))

    # a new release later joins the line, story order kept
    view[2]["mirrors"][0]["chapters"].append("3")
    rows = ap.candidates(ap.load(root), view, lambda k: k, set(), tomorrow)
    ch = {x["series_id"]: x for x in rows}
    check("a new release is picked up after the window", ch["charlie"]["next"] == "2"
          and ch["charlie"]["remaining"] == ["2", "3"])

    # waits: busy queue and budget
    fake.busy = True
    check("waits while an ingest is running", ap.tick(root, fake.deps(), tomorrow) is None
          and "running" in ap.runtime["last_result"])
    fake.busy = False
    fake.spent = 5.9
    check("waits when one more chapter would pass the $6 cap",
          ap.tick(root, fake.deps(), tomorrow) is None and "budget" in ap.runtime["last_result"])
    fake.spent = 0.0

    # owner stop: never re-picked by itself
    n = len(fake.queued)
    ap.on_job_end(root, f"job{n}", "cancelled", "stopped by user")
    rows = {x["series_id"]: x for x in ap.candidates(ap.load(root), view, lambda k: k, set(), tomorrow)}
    check("a chapter you stopped is not picked again automatically", rows["bravo"]["state"] == "stopped")
    ap.set_series(root, "bravo", "retry")
    rows = {x["series_id"]: x for x in ap.candidates(ap.load(root), view, lambda k: k, set(), tomorrow)}
    check("Retry makes it pickable again", rows["bravo"]["state"] == "ready" and rows["bravo"]["next"] == "5")

    # pause a series
    ap.set_series(root, "alpha", "pause")
    rows = {x["series_id"]: x for x in ap.candidates(ap.load(root), view, lambda k: k, set(), tomorrow)}
    check("a paused series is skipped", rows["alpha"]["state"] == "paused")
    ap.set_series(root, "alpha", "resume")

    # failures: cooldown, then blocked after 2
    r = ap.tick(root, fake.deps(), tomorrow)
    jid = f"job{len(fake.queued)}"
    ap.on_job_end(root, jid, "error", "source timed out")
    rows = {x["series_id"]: x for x in ap.candidates(ap.load(root), view, lambda k: k, set(), time.time() + 60)}
    check("one failure: waits an hour before retrying", rows[r and next(
        k for k, v in rows.items() if v["title"] == r["series"])]["state"] == "cooldown")
    key = [k for k, e in ap.load(root)["ledger"].items() if e.get("job") == jid][0]
    ap.record_queued(root, key, series_id=ap.load(root)["ledger"][key]["series_id"],
                     chapter=ap.load(root)["ledger"][key]["chapter"], project="p", job=jid,
                     source="autopilot", url="u")
    ap.on_job_end(root, jid, "error", "source timed out")
    sid = ap.load(root)["ledger"][key]["series_id"]
    rows = {x["series_id"]: x for x in ap.candidates(ap.load(root), view, lambda k: k, set(), tomorrow + 7200)}
    check("two failures block the series (story order) with the error", rows[sid]["state"] == "blocked"
          and "timed out" in rows[sid]["reason"])

    # never repeat: a done chapter whose project was deleted stays made
    ledger = ap.load(root)["ledger"]
    done = [e for e in ledger.values() if e["status"] == "done" and e["series_id"] == "alpha"]
    rows = {x["series_id"]: x for x in ap.candidates(ap.load(root), view, lambda k: k, set(), tomorrow)}
    check("never repeat: made chapters are not offered again (even if the project is gone)",
          all(e["chapter"] not in rows["alpha"]["remaining"] for e in done) and done)

    check("cost estimate learns from finished chapters", abs(ap.estimate(ap.load(root)["ledger"]) - 0.3) < 1e-9)
    try:
        ap.update_settings(root, {"per_day": 99})
        check("per-day limit is bounded", False)
    except ValueError:
        check("per-day limit is bounded", True)

    # archive
    import project_archive as arch
    pdir = tempfile.mkdtemp(prefix="arch_")
    check("not published -> not archived", arch.is_published({}) is False)
    check("a published record counts", arch.is_published(
        {"final.mp4": {"results": [{"status": "failed"}, {"status": "published"}]}}))
    arch.archive(pdir, "published", now)
    check("archived with a delete date 14 days out", arch.view(pdir, now)["days_left"] == 14
          and not arch.due(pdir, now + 13 * 86400) and arch.due(pdir, now + 14 * 86400 + 1))
    arch.keep(pdir)
    check("Keep cancels the delete", not arch.due(pdir, now + 400 * 86400))
    arch.unarchive(pdir)
    check("Unarchive removes it", arch.read(pdir) is None)

    server_checks()


def server_checks():
    """Wiring: routes, resume, stop of a waiting job, inbox status."""
    import ingest
    import server
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="apsrv_")
    work = tempfile.mkdtemp(prefix="apwork_")
    jobs_dir = os.path.join(root, "_jobs")
    os.makedirs(jobs_dir)
    saved = (ingest.PROJECTS, server.WORK, server._JOBS_DIR, server._enqueue_ingest)
    ingest.PROJECTS, server.WORK, server._JOBS_DIR = root, work, jobs_dir
    server._ACTIVE_CACHE["key"] = None
    queued = []
    real_enqueue = saved[3]

    def fake_enqueue(url, fresh=False, engine="gemini", variant="", direct=None,
                     source="manual", job_id=None, control="run", why=""):
        queued.append((url, job_id, source, why))
        return job_id or "new"
    server._enqueue_ingest = fake_enqueue
    try:
        c = TestClient(server.app)
        d = c.get("/api/autopilot")
        check("GET /api/autopilot answers with the card state", d.status_code == 200
              and d.json()["enabled"] is False and "undo" in d.json())
        r = c.post("/api/autopilot/settings", json={"per_day": 4, "enabled": False})
        check("settings save", r.status_code == 200 and r.json()["settings"]["per_day"] == 4)
        check("bad per-day refused", c.post("/api/autopilot/settings", json={"per_day": 500}).status_code == 400)

        # a cap-paused ingest from an earlier day: stop retires it, resume re-queues
        rec = {"status": "budget_paused", "url": "https://x/comics/s/chapter/1", "ts": time.time(),
               "paused_day": "2000-01-01", "engine": "gemini", "control": "run"}
        json.dump(rec, open(os.path.join(jobs_dir, "jp1.json"), "w"))
        r = c.post("/api/jobs/resume", json={"job_id": "jp1"})
        check("▶ Resume re-queues a cap-paused ingest under the same record",
              r.status_code == 200 and queued and queued[-1][1] == "jp1")
        json.dump(rec, open(os.path.join(jobs_dir, "jp2.json"), "w"))
        r = c.post("/api/jobs/control", json={"job_id": "jp2", "action": "stop"})
        check("⏹ Stop retires a waiting (cap-paused) ingest",
              r.status_code == 200 and server.INGEST["jp2"]["status"] == "cancelled")
        check("resuming a finished job is refused",
              c.post("/api/jobs/resume", json={"job_id": "nope"}).status_code == 404)

        # boot sweep: recent cut-off job -> interrupted -> resumed once
        json.dump({"status": "running", "url": "https://x/comics/s/chapter/2", "ts": time.time(),
                   "updated": time.time(), "engine": "gemini"}, open(os.path.join(jobs_dir, "jr.json"), "w"))
        json.dump({"status": "running", "url": "https://x/comics/s/chapter/3", "ts": 1,
                   "updated": 1}, open(os.path.join(jobs_dir, "jold.json"), "w"))
        server._sweep_orphaned_ingest_jobs()
        check("restart: a recent ingest is marked to resume, an old one written off",
              json.load(open(os.path.join(jobs_dir, "jr.json")))["status"] == "interrupted"
              and json.load(open(os.path.join(jobs_dir, "jold.json")))["status"] == "error")
        n = server._resume_interrupted()
        check("startup resumes it once, counting the resume",
              n == 1 and queued[-1][1] == "jr" and server.INGEST["jr"]["resumes"] == 1)

        # inbox status
        pdir = os.path.join(root, "series-a_1")
        os.makedirs(os.path.join(pdir, "exports"))
        json.dump([], open(os.path.join(pdir, "segments.json"), "w"))
        json.dump({"id": "series-a_1", "series": "Series A", "chapter": "1",
                   "url": "https://example.com/a/1"}, open(os.path.join(pdir, "project.json"), "w"))
        lst = {p["id"]: p for p in c.get("/api/projects").json()["projects"]}
        check("a new chapter shows as Ready for review", lst["series-a_1"].get("review_status") == "ready")
        json.dump({"approved": True}, open(os.path.join(pdir, "storyboard.json"), "w"))
        lst = {p["id"]: p for p in c.get("/api/projects").json()["projects"]}
        check("approved shows as Approved", lst["series-a_1"]["review_status"] == "approved")
        json.dump({"final.mp4": {"results": [{"status": "published"}]}},
                  open(os.path.join(pdir, server.PUBLISHES_NAME), "w"))
        out = server._archive_sweep()
        lst = {p["id"]: p for p in c.get("/api/projects").json()["projects"]}
        check("published -> archived by the sweep, with a delete date",
              out["archived"] == ["series-a_1"] and lst["series-a_1"]["review_status"] == "archived"
              and lst["series-a_1"]["archive"]["days_left"] == 14)
        r = c.post("/api/projects/archive", json={"id": "series-a_1", "action": "keep"})
        check("Keep via the API", r.status_code == 200 and r.json()["archive"]["keep"] is True)
        r = c.post("/api/projects/archive", json={"id": "../etc", "action": "keep"})
        check("archive action refuses a path id", r.status_code == 400)
    finally:
        ingest.PROJECTS, server.WORK, server._JOBS_DIR, server._enqueue_ingest = saved
        server._ACTIVE_CACHE["key"] = None
        for k in ("jp1", "jp2", "jr", "jold"):
            server.INGEST.pop(k, None)

    src = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    check("the page never adopts or auto-opens an autopilot chapter over your board",
          "&& x.source !== 'autopilot'" in src and "s.source !== 'autopilot') await activateProj" in src)
    check("Logs offer ▶ resume for stopped, failed, cap-paused and restart-cut jobs",
          "function jobResume(id)" in src and "/api/jobs/resume" in src)
    check("Ingest page has the Autopilot card with an off switch and its undo line",
          'id="apcard"' in src and "Switch off" in src and "d.undo" in src)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
