"""Render queue (owner, 2026-10-05): several chapters render one after another,
in order, each in its own voice; failures don't stop the rest; a restart
picks the queue back up. Temp projects root; the render itself is faked
(no ffmpeg, no TTS, no money)."""
import json
import os
import sys
import tempfile
import threading
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
    import render_queue as rq
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="rq_")
    work = tempfile.mkdtemp(prefix="rqw_")
    pids = ["series-a_1", "series-b_2", "series-c_3"]
    for pid in pids:
        os.makedirs(os.path.join(root, pid))
        json.dump([], open(os.path.join(root, pid, "segments.json"), "w"))
        json.dump({"id": pid, "url": f"https://example.com/{pid}"}, open(os.path.join(root, pid, "project.json"), "w"))

    # --- the list itself
    it, why = rq.add(root, "series-a_1")
    check("add queues a chapter, in its own voice by default", it and it["status"] == "waiting" and it["keep_voice"])
    check("the same chapter can't be queued twice", rq.add(root, "series-a_1") == (None, "already in the render queue"))
    rq.add(root, "series-b_2", keep_voice=False)
    check("position counts the line", rq.position(root, "series-b_2")["place"] == 2)
    rq.update(root, it["id"], status="rendering", job="gone", attempts=1)
    ok, why = rq.remove(root, it["id"])
    check("a rendering chapter can't just be removed (Stop it instead)", not ok and "Stop" in why)
    rq.recover(root, lambda j: False)
    check("after a restart a dead 'rendering' row goes back to waiting",
          rq.load(root)["items"][0]["status"] == "waiting")
    rq.update(root, it["id"], status="rendering", job="gone", attempts=2)
    rq.recover(root, lambda j: False)
    check("...but not forever", rq.load(root)["items"][0]["status"] == "error")
    check("clear removes finished rows only", rq.clear_finished(root) == 1 and len(rq.load(root)["items"]) == 1)
    os.remove(os.path.join(root, rq.FILE))

    # --- the worker, with the render faked
    saved = (ingest.PROJECTS, server.WORK, server.storyboard_approve, server._RQ_POLL, server._ingest_mod.PROJECTS)
    ingest.PROJECTS, server.WORK = root, work
    server._ingest_mod.PROJECTS = root
    server._ACTIVE_CACHE["key"] = None
    server._RQ_POLL = 0.05
    order, overlap, live = [], [], {"n": 0}

    def fake_approve(body):
        pid = server.get_active_project_id()
        order.append((pid, body.keep_voice))
        if pid == "series-b_2":
            raise server.HTTPException(400, "timeline has a gap")
        jid = "fj_" + pid
        server.JOBS[jid] = {"type": "finalize", "status": "running", "project": pid}
        live["n"] += 1
        overlap.append(live["n"])

        def finish():
            time.sleep(0.2)
            live["n"] -= 1
            server.JOBS[jid]["status"] = "done"
        threading.Thread(target=finish, daemon=True).start()
        return {"job": jid}
    server.storyboard_approve = fake_approve
    try:
        c = TestClient(server.app)
        r = c.post("/api/render-queue/add", json={"projects": ["series-a_1", "nope_9"]}).json()
        check("a chapter whose board isn't built is refused with the reason",
              r["added"] == [] and {x["id"] for x in r["skipped"]} == {"series-a_1", "nope_9"})
        for pid in pids:
            rq.add(root, pid)
        server._rq_kick()
        t0 = time.time()
        while time.time() - t0 < 10 and (rq.next_waiting(root) or any(i["status"] == "rendering" for i in rq.load(root)["items"])):
            time.sleep(0.05)
        st = {i["project"]: i for i in rq.load(root)["items"]}
        check("chapters render in the order queued", [p for p, _ in order] == pids)
        check("one at a time (never two renders at once)", overlap and max(overlap) == 1)
        check("queued renders keep each chapter's own voice (free)", all(kv for _, kv in order))
        check("a chapter that can't start is marked failed with the reason, and the rest carry on",
              st["series-b_2"]["status"] == "error" and "gap" in st["series-b_2"]["error"]
              and st["series-a_1"]["status"] == "done" and st["series-c_3"]["status"] == "done")
        v = c.get("/api/render-queue").json()["items"]
        check("the list shows names for every row", all(i["name"] for i in v))
        rq.add(root, "series-a_1")
        server.JOBS["block"] = {"type": "finalize", "status": "running", "project": "series-c_3"}
        bar = c.get("/api/jobsbar?failed=1").json()["jobs"]
        check("the studio jobs bar shows waiting chapters", any(j["status"] == "in_queue" for j in bar))
        check("the classic jobs bar doesn't", not any(j["status"] == "in_queue" for j in c.get("/api/jobsbar").json()["jobs"]))
        server.JOBS["block"]["status"] = "done"
    finally:
        ingest.PROJECTS, server.WORK, server.storyboard_approve, server._RQ_POLL, server._ingest_mod.PROJECTS = saved
        server._ACTIVE_CACHE["key"] = None
        for k in [k for k in server.JOBS if k.startswith("fj_") or k == "block"]:
            server.JOBS.pop(k, None)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
