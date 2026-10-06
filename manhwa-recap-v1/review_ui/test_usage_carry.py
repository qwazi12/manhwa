"""Worker threads count under their job (owner, 2026-10-05: "why the works keep
pausing" — voice lines recorded 4-at-a-time all landed in one shared
'unknown' job, whose per-job cap then paused every new chapter)."""
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import usage
    seen = []

    def work(_):
        seen.append(usage.get_job_id())

    def job(jid, wrap):
        usage.set_job(jid)
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(usage.carry(work) if wrap else work, range(8)))

    t = threading.Thread(target=job, args=("chapterA", False)); t.start(); t.join()
    check("without carry a worker thread loses the job (the bug)", set(seen) == {"unknown"})
    seen.clear()
    t = threading.Thread(target=job, args=("chapterB", True)); t.start(); t.join()
    check("with carry every worker counts under its own job", set(seen) == {"chapterB"})
    seen.clear()
    a = threading.Thread(target=job, args=("x1", True)); b = threading.Thread(target=job, args=("x2", True))
    a.start(); b.start(); a.join(); b.join()
    check("two jobs at once keep their own ids", set(seen) == {"x1", "x2"} and len(seen) == 16)
    src = {f: open(os.path.join(HERE, f), encoding="utf-8").read() for f in ("ingest.py", "server.py")}
    src["shot_planner.py"] = open(os.path.join(os.path.dirname(HERE), "shot_planner.py"), encoding="utf-8").read()
    check("voice lines while making use it", "synth = usage.carry(srv._synth_rest)" in src["ingest.py"])
    check("re-voicing and the shot planner use it",
          "ex.map(usage.carry(one), beats)" in src["server.py"] and "_usage.carry(process_panel)" in src["shot_planner.py"])
    check("a render sets its own job id", "usage.set_job(job_id)          # a re-voice counts" in src["server.py"])


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
