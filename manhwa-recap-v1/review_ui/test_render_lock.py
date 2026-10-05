"""While a chapter renders, no other chapter can become the open one: a render
reads the open chapter's timeline clip by clip, so switching mid-render would
mix two chapters (2026-10-05). Temp projects root, fake job records, no network."""
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
    import ingest
    import server
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="rl_")
    work = tempfile.mkdtemp(prefix="rlw_")
    for pid in ("series-a_1", "series-b_2"):
        os.makedirs(os.path.join(root, pid))
        json.dump([], open(os.path.join(root, pid, "segments.json"), "w"))
        json.dump({"id": pid, "url": f"https://example.com/{pid}"}, open(os.path.join(root, pid, "project.json"), "w"))
    saved = (ingest.PROJECTS, server.WORK)
    ingest.PROJECTS, server.WORK = root, work
    server._ACTIVE_CACHE["key"] = None
    try:
        c = TestClient(server.app)
        check("nothing rendering -> a chapter opens", c.post("/api/activate", json={"id": "series-a_1"}).status_code == 200)
        server.JOBS["rl1"] = {"type": "finalize", "status": "running", "project": "series-a_1"}
        r = c.post("/api/activate", json={"id": "series-b_2"})
        check("another chapter can't be opened while one renders (409 with the reason)",
              r.status_code == 409 and "rendering" in r.json().get("detail", ""))
        check("the rendering chapter stays open", server.get_active_project_id() == "series-a_1")
        check("re-opening the rendering chapter itself is fine",
              c.post("/api/activate", json={"id": "series-a_1"}).status_code == 200)
        b = server.board_view("series-b_2")
        check("its board still shows, marked view-only",
              b.get("locked") and b.get("active") is False and server.get_active_project_id() == "series-a_1")
        server.JOBS["rl1"]["status"] = "error"
        server.JOBS["rl2"] = {"status": "running", "project": "series-a_1", "seg_indices": [1]}
        check("a clip-only render locks too", c.post("/api/activate", json={"id": "series-b_2"}).status_code == 409)
        server.JOBS["rl2"]["status"] = "done"
        check("render finished -> other chapters open again",
              c.post("/api/activate", json={"id": "series-b_2"}).status_code == 200
              and not server.board_view("series-b_2").get("locked"))
    finally:
        ingest.PROJECTS, server.WORK = saved
        server._ACTIVE_CACHE["key"] = None
        server.JOBS.pop("rl1", None)
        server.JOBS.pop("rl2", None)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
