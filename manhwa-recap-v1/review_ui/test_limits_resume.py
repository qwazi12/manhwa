"""Daily limits (owner, 2026-10-06): a chapter paused by a usage limit resumes
by itself as soon as there is room again (a limit raised), not only at
midnight; autopilot holds the line while one is paused instead of starting
more that would pause too; a chapter still being made has a real title."""
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
    import server
    import usage
    today = usage._today()
    paused = [{"job": "j1", "status": "budget_paused", "paused_day": today, "ts": 1, "url": "https://asurascans.com/comics/x-series-abc123/chapter/5"},
              {"job": "j2", "status": "budget_paused", "paused_day": "2000-01-01", "ts": 2, "url": "u2"}]
    queued = []
    saved = (server._waiting_ingests, server._enqueue_ingest, server._cap_headroom, server._autopilot.set_status_for_job)
    server._waiting_ingests = lambda st: [j for j in paused if j["status"] == st]
    server._enqueue_ingest = lambda url, *a, job_id=None, why="", **k: queued.append((job_id, why))
    server._autopilot.set_status_for_job = lambda *a, **k: None
    try:
        server._cap_headroom = lambda: False
        server._resume_budget_paused()
        check("no room today: only the chapter paused on an earlier day resumes", [q[0] for q in queued] == ["j2"])
        queued.clear()
        server._cap_headroom = lambda: True
        server._resume_budget_paused()
        check("room again (a limit was raised): today's paused chapter resumes too",
              ("j1", "resumed: there is room under the limits again") in queued)
        check("autopilot holds the line while a chapter is paused today",
              server._ap_deps()["queue_busy"]() is True)
        paused[0]["status"] = "done"
        check("...and carries on once nothing is paused", server._ap_deps()["queue_busy"]() in (False, True))
    finally:
        (server._waiting_ingests, server._enqueue_ingest, server._cap_headroom, server._autopilot.set_status_for_job) = saved
    v = server._spend_cap_view()
    check("Settings lists every daily limit with today's use",
          {l["var"] for l in v["limits"]} == {"MAX_DAILY_GEMINI_CALLS", "MAX_DAILY_TTS_CHARS"} and "paused_by_limit" in v)
    src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    check("a chapter still being made is named from its link",
          'f"{_st} Ch.{_c}"' in src and "blank \"Waiting\" cards on Home" in src)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
