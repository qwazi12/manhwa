"""Opening a chapter from Projects lands on ITS board, the Projects list marks
the chapter that is really open, and /review opened bare shows the newest
video in one answer. Temporary projects root; no network."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import ingest
    import server
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="proj_")
    work = tempfile.mkdtemp(prefix="work_")
    for pid, ch, has_video in (("series-a_1", "1", True), ("series-b_2", "2", False)):
        d = os.path.join(root, pid)
        os.makedirs(os.path.join(d, "exports"))
        json.dump([], open(os.path.join(d, "segments.json"), "w"))
        json.dump({"id": pid, "series": pid.split("_")[0], "chapter": ch,
                   "url": "https://example.com/" + pid}, open(os.path.join(d, "project.json"), "w"))
        if has_video:
            open(os.path.join(d, "exports", "final_A.mp4"), "wb").write(b"\0" * 10)
    saved = (ingest.PROJECTS, server.WORK)
    ingest.PROJECTS, server.WORK = root, work
    server._ACTIVE_CACHE["key"] = None
    try:
        c = TestClient(server.app)
        r = c.post("/api/activate", json={"id": "series-b_2"})
        check("activating a chapter succeeds", r.status_code == 200)
        lst = c.get("/api/projects").json()["projects"]
        act = [p["id"] for p in lst if p.get("active")]
        check("Projects marks the chapter that is really open", act == ["series-b_2"])
        check("the legacy placeholder is not listed while a chapter is open",
              not any(p["id"] == "chapter-2 (current)" for p in lst))
        rv = c.get("/api/review").json()
        check("/review opened bare, chapter has no video -> newest video from any chapter",
              rv.get("project") == "series-a_1" and rv.get("name") == "final_A.mp4")
        rv2 = c.get("/api/review?project=series-b_2").json()
        check("asking for a specific chapter still reports it has no video",
              rv2.get("missing") is True)
    finally:
        ingest.PROJECTS, server.WORK = saved
        server._ACTIVE_CACHE["key"] = None

    src = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    i = src.index("async function activateProj(id)")
    body = src[i:src.index("}}\n", src.index("location.href", i)) + 3]
    check("Open goes to the board (not a reload that keeps #projects)",
          "location.href = '/storyboard'" in body and "location.reload()" not in body)

    for n, ok in R:
        print(("PASS " if ok else "FAIL ") + n)
    n_ok = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n_ok, len(R)))
    return 0 if n_ok == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
