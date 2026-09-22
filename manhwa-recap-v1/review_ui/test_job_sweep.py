"""Dead render jobs must retire themselves, and a project renders once at a time.

Observed 2026-09-22 on the-extras-academy-survival-guide-6465_2:
  job 830f59c7ca30 — done 37/96, control="stop", still "running" 9.2 HOURS later
  job bac12627bb3f — done 0/59, "running" for 1.9 hours on the SAME project

Three defects produced that:
1. `control="stop"` is cooperative — only a LIVE worker reaching _control_gate
   acts on it. A dead worker leaves the record "running" forever.
2. The only sweep ran at BOOT, so with no deploy since, nothing retired them.
3. Ingest had `_active_ingest_for_url` to stop duplicate runs; render had no
   equivalent, so a second render could start on top of the first and both
   wrote the same clips directory.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import server

    d = server._jobs_dir()
    now = time.time()
    written = []

    def put(jid, **kw):
        rec = {"job": jid, "status": "running", "done": 1, "total": 10}
        rec.update(kw)
        fn = f"render_{jid}.json"
        written.append(fn)
        json.dump(rec, open(os.path.join(d, fn), "w"))
        return fn

    def read(jid):
        return json.load(open(os.path.join(d, f"render_{jid}.json")))

    try:
        # a live job, recently seen — must be left completely alone
        put("swlive", heartbeat=now - 30, ts=now - 300, project="p_live")
        # stopped by the operator, worker never acknowledged
        put("swstop", heartbeat=now - 9 * 3600, ts=now - 9 * 3600,
            control="stop", done=37, total=96, project="p_stop")
        # silent far longer than the stall window
        put("swdead", heartbeat=now - 4 * 3600, ts=now - 4 * 3600,
            done=0, total=59, project="p_dead")
        # stop requested one second ago — still inside the grace period
        put("swfresh", heartbeat=now - 1, ts=now - 60, control="stop",
            project="p_fresh")

        server._sweep_stalled_jobs()

        check("a job that checked in recently is untouched",
              read("swlive")["status"] == "running")
        check("a stop the worker never acknowledged is applied",
              read("swstop")["status"] == "cancelled")
        check("...and says the worker did not acknowledge",
              "did not acknowledge" in (read("swstop").get("error") or ""))
        check("a job silent for hours is declared dead",
              read("swdead")["status"] == "error")
        check("...naming how long it was silent",
              "no progress for" in (read("swdead").get("error") or ""))
        check("...and telling the operator cached work survives",
              "resume" in (read("swdead").get("error") or ""))
        check("a stop inside the grace period is NOT pre-empted",
              read("swfresh")["status"] == "running")

        # progress must reset the clock, or long renders get killed mid-flight
        server.JOBS["swprog"] = {"status": "running", "done": 3, "total": 10}
        server._persist_job("swprog")
        written.append("render_swprog.json")
        rec = read("swprog")
        check("every job write stamps a heartbeat",
              rec.get("heartbeat") and now - rec["heartbeat"] < 60)
        check("...and a ts, so it can never sort as 0 again", rec.get("ts"))
        server._sweep_stalled_jobs()
        check("a job that just reported progress survives the sweep",
              read("swprog")["status"] == "running")

        # ---- one render per project
        server.JOBS["rjob"] = {"status": "running", "project": "proj_x",
                               "heartbeat": time.time(), "ts": time.time()}
        check("an in-flight render is found for its project",
              server._active_render_for_project("proj_x") == "rjob")
        check("...and a different project is unaffected",
              server._active_render_for_project("proj_y") is None)
        server.JOBS["rjob"]["status"] = "done"
        check("a finished render no longer blocks the next one",
              server._active_render_for_project("proj_x") is None)

        src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
        check("starting a render checks for one already running",
              "_active_render_for_project(project)" in src)
        check("...and refuses with 409 rather than racing it",
              "a render is already running for" in src)
        check("the sweep runs on every job listing, not only at boot",
              "_sweep_stalled_jobs()\n    recs = []" in src)
    finally:
        for fn in written:
            try:
                os.remove(os.path.join(d, fn))
            except OSError:
                pass
        for k in ("swprog", "rjob"):
            server.JOBS.pop(k, None)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
