"""Operator control tests (Session 27): pause, stop, delete, queue.

Jobs are cooperative, not preemptive — a worker only notices pause/stop when it
next reports progress. These tests pin that contract, and the rule that a job
still doing work cannot be deleted out from under itself.

Run: python3 test_job_control.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import server as srv


def main():
    r = []
    srv._PAUSE_POLL = 0.02              # keep the pause test quick
    noop = lambda _jid: None

    # ---- stop raises inside the worker
    srv.INGEST.clear(); srv.JOBS.clear()
    srv.INGEST["a"] = {"status": "running", "control": "stop"}
    try:
        srv._control_gate(srv.INGEST, "a", noop); raised = False
    except srv.JobCancelled:
        raised = True
    r.append(("a stopped job raises inside the worker", raised))

    # ---- pause blocks, then resume releases
    srv.INGEST["b"] = {"status": "running", "control": "pause"}
    released = []
    def waiter():
        try:
            srv._control_gate(srv.INGEST, "b", noop); released.append("resumed")
        except srv.JobCancelled:
            released.append("stopped")
    t = threading.Thread(target=waiter); t.start()
    time.sleep(0.15)
    r.append(("a paused job reports paused while it waits",
              srv.INGEST["b"]["status"] == "paused" and not released))
    srv.INGEST["b"]["control"] = "run"
    t.join(timeout=3)
    r.append(("resume releases it and restores running",
              released == ["resumed"] and srv.INGEST["b"]["status"] == "running"))

    # ---- pause then stop escapes the wait rather than hanging
    srv.INGEST["c"] = {"status": "running", "control": "pause"}
    out = []
    def waiter2():
        try:
            srv._control_gate(srv.INGEST, "c", noop); out.append("resumed")
        except srv.JobCancelled:
            out.append("stopped")
    t2 = threading.Thread(target=waiter2); t2.start()
    time.sleep(0.1)
    srv.INGEST["c"]["control"] = "stop"
    t2.join(timeout=3)
    r.append(("stopping a PAUSED job still ends it", out == ["stopped"]))

    # ---- a clean job passes straight through
    srv.INGEST["d"] = {"status": "running", "control": "run"}
    srv._control_gate(srv.INGEST, "d", noop)
    r.append(("an uncontrolled job is not delayed", srv.INGEST["d"]["status"] == "running"))

    # ---- the control endpoint
    srv.INGEST["e"] = {"status": "running", "control": "run"}
    srv.job_control(srv.JobControlIn(job_id="e", action="pause"))
    r.append(("pause sets the flag and marks it pausing",
              srv.INGEST["e"]["control"] == "pause"))
    srv.job_control(srv.JobControlIn(job_id="e", action="resume"))
    r.append(("resume clears the flag", srv.INGEST["e"]["control"] == "run"))
    srv.job_control(srv.JobControlIn(job_id="e", action="stop"))
    r.append(("stop sets the stop flag", srv.INGEST["e"]["control"] == "stop"))

    srv.INGEST["f"] = {"status": "done", "control": "run"}
    try:
        srv.job_control(srv.JobControlIn(job_id="f", action="stop")); code = None
    except Exception as ex:
        code = getattr(ex, "status_code", None)
    r.append(("a finished job cannot be stopped (409)", code == 409))
    try:
        srv.job_control(srv.JobControlIn(job_id="nope", action="stop")); code = None
    except Exception as ex:
        code = getattr(ex, "status_code", None)
    r.append(("an unknown job id is a 404", code == 404))
    try:
        srv.job_control(srv.JobControlIn(job_id="e", action="explode")); code = None
    except Exception as ex:
        code = getattr(ex, "status_code", None)
    r.append(("an unknown action is rejected (400)", code == 400))

    # ---- delete: finished go, live ones are protected
    srv.INGEST.clear(); srv.JOBS.clear()
    srv.INGEST["g"] = {"status": "done"}
    srv.INGEST["h"] = {"status": "running"}
    srv.JOBS["i"] = {"status": "error"}
    res = srv.jobs_delete(srv.JobDelIn(job_ids=["g", "h", "i"]))
    r.append(("finished and errored records are deleted",
              set(res["deleted"]) == {"g", "i"}))
    r.append(("a running job is protected, with a reason",
              [x["id"] for x in res["skipped"]] == ["h"]
              and "stop it first" in res["skipped"][0]["reason"]))
    r.append(("...and it survives in the store", "h" in srv.INGEST))

    # ---- queue runs one at a time
    srv.INGEST.clear(); srv._QUEUE.clear(); srv._QUEUE_RUNNING = False
    order, running = [], []
    def fake_run(job_id, url, fresh=False):
        running.append(job_id)
        r.append(("only one queued ingest runs at a time", len(running) == 1)) if len(running) > 1 else None
        time.sleep(0.05); order.append(url); running.remove(job_id)
        srv.INGEST[job_id]["status"] = "done"
    srv._run_ingest_job = fake_run
    srv._persist_ingest = noop
    ids = [srv._enqueue_ingest(f"http://x/chapter/{n}") for n in (1, 2, 3)]
    for _ in range(200):
        if all(srv.INGEST[i].get("status") == "done" for i in ids): break
        time.sleep(0.02)
    r.append(("every queued chapter runs", len(order) == 3))
    r.append(("...in the order they were queued",
              order == ["http://x/chapter/1", "http://x/chapter/2", "http://x/chapter/3"]))

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
