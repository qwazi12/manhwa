"""At most 2 chapters in the line (owner, 2026-10-06): the rest wait their turn
("held") and start by themselves, oldest first; a restart or a manual Resume
never overfills it; a chapter paused by a limit waits for room."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import server
    saved = (dict(server.INGEST), server._enqueue_ingest, server._persist_ingest, server._load_ingest,
             server._all_ingest_jobs, server._cap_headroom, server._autopilot.set_status_for_job)
    store = {}

    def enq(url, *a, job_id=None, why="", **k):
        store[job_id].update(status="queued", msg=why)
        server.INGEST[job_id] = store[job_id]
        return job_id
    server._enqueue_ingest = enq
    server._persist_ingest = lambda jid: store.__setitem__(jid, server.INGEST[jid])
    server._load_ingest = lambda jid: dict(store.get(jid) or {}) or None
    server._all_ingest_jobs = lambda: [dict(v, job=k) for k, v in store.items()]
    server._autopilot.set_status_for_job = lambda *a, **k: None
    server._cap_headroom = lambda: True
    try:
        server.INGEST.clear()
        for i, st in enumerate(["running", "queued", "queued", "queued"]):
            store[f"j{i}"] = {"status": st, "ts": i, "url": f"u{i}"}
            server.INGEST[f"j{i}"] = store[f"j{i}"]
        server._QUEUE[:] = [(f"j{i}", "u", False, "gemini", "", None) for i in (1, 2, 3)]
        server._trim_line()
        check("an over-full line keeps 2; the newest wait their turn",
              server._line_count() == 2 and store["j3"]["status"] == "held" and store["j2"]["status"] == "held"
              and [q[0] for q in server._QUEUE] == ["j1"])
        server._fill_line()
        check("nothing more starts while 2 are in line", store["j2"]["status"] == "held")
        store["j0"]["status"] = "done"
        server._fill_line()
        check("when one finishes, the oldest waiting one starts", store["j2"]["status"] == "queued" and store["j3"]["status"] == "held")
        store["j4"] = {"status": "budget_paused", "ts": 0.5, "paused_day": "2000-01-01", "url": "u4"}
        store["j1"]["status"] = "done"; store["j2"]["status"] = "done"
        server._cap_headroom = lambda: False
        server._fill_line()
        check("a chapter paused by a limit waits for room; one waiting its turn goes", store["j4"]["status"] == "budget_paused" and store["j3"]["status"] == "queued")
        server._cap_headroom = lambda: True
        server._fill_line()
        check("...and goes when there is room", store["j4"]["status"] == "queued")
        check("autopilot waits while a chapter is held",
              (store.__setitem__("j5", {"status": "held", "ts": 9}) or server.INGEST.__setitem__("j5", store["j5"]) or True)
              and server._ap_deps()["queue_busy"]() is True)
    finally:
        server.INGEST.clear(); server.INGEST.update(saved[0])
        (server._enqueue_ingest, server._persist_ingest, server._load_ingest, server._all_ingest_jobs,
         server._cap_headroom, server._autopilot.set_status_for_job) = saved[1:]
        server._QUEUE[:] = []
    import chapter_status as cs
    check("a held chapter shows as being made, not 'needs you'", cs.decide({"ingest_status": "held"})["key"] == "making")
    check("a limit pause reads in plain words", "daily limit" in cs.decide({"ingest_status": "budget_paused"})["reason"])


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
