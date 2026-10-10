"""Test multi-chapter compilation merging into 1 long-form video with SEO & queue cleanup."""
import os
import sys
import json
import tempfile
from fastapi.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest
import server
import publish_queue as pq
import range_compile as rc

def test_range_merge():
    root = tempfile.mkdtemp()
    saved = ingest.PROJECTS
    try:
        ingest.PROJECTS = root
        for ch in (23, 24, 25):
            pid = f"regressor_{ch}"
            pdir = os.path.join(root, pid)
            os.makedirs(os.path.join(pdir, "exports"), exist_ok=True)
            json.dump({"id": pid, "series": "A Regressors Tale of Cultivation", "series_id": "regressor", "chapter": str(ch)},
                      open(os.path.join(pdir, "project.json"), "w"))
            open(os.path.join(pdir, "exports", "final.mp4"), "wb").write(b"video")
            # Mark them approved (ready to post)
            json.dump({"final.mp4": {"status": "approved"}}, open(os.path.join(pdir, "reviews.json"), "w"))

        # Add chapter 23 and 24 to the posting schedule queue
        pq._save(root, {
            "items": [
                {"id": "q1", "project": "regressor_23", "name": "final.mp4", "status": "queued"},
                {"id": "q2", "project": "regressor_24", "name": "final.mp4", "status": "queued"},
                {"id": "q3", "project": "other_1", "name": "final.mp4", "status": "queued"}
            ],
            "slots_done": {}
        })

        client = TestClient(server.app)

        # Before merge: studio_overview shows them in ready
        st_before = client.get("/api/studio").json()
        ready_pids = [r["project"] for r in st_before["ready"]]
        assert "regressor_25" in ready_pids, "Chapter 25 should be in ready before merge"

        # Plan
        res = client.post("/api/ranges/plan", json={"projects": ["regressor_23", "regressor_24", "regressor_25"]})
        assert res.status_code == 200
        data = res.json()
        assert data["chapters"] == [23, 24, 25]
        assert data["ready"] is True

        # Build (fake stitch and probe)
        orig_stitch = rc.stitch
        orig_probe = rc._probe
        rc.stitch = lambda chs, out: [(23, 0.0), (24, 60.0), (25, 120.0)]
        rc._probe = lambda path: 180.0
        try:
            build_res = client.post("/api/ranges/build", json={"projects": ["regressor_23", "regressor_24", "regressor_25"]})
            assert build_res.status_code == 200
            bdata = build_res.json()
            assert bdata["ok"] is True
            assert bdata["project"] == "regressor_ch23-25"

            # 1. Verify constituent chapters were REMOVED from the active queue!
            active_q = [it["id"] for it in pq.load(root)["items"] if it["status"] in pq.ACTIVE]
            assert "q1" not in active_q and "q2" not in active_q, f"q1 and q2 must be removed from queue! Got: {active_q}"
            assert active_q == ["q3"], f"Only other_1 should remain in queue! Got: {active_q}"

            # 2. Verify constituent chapters were marked 'merged' in reviews.json!
            for ch in (23, 24, 25):
                rev = json.load(open(os.path.join(root, f"regressor_{ch}", "reviews.json")))
                assert rev["final.mp4"]["status"] == "merged"
                assert rev["final.mp4"]["merged_into"] == "regressor_ch23-25"

            # 3. Verify constituent chapters are REMOVED from studio_overview 'ready' and 'review'!
            st_after = client.get("/api/studio").json()
            ready_after = [r["project"] for r in st_after["ready"]]
            review_after = [r["project"] for r in st_after["review"]]
            for ch in (23, 24, 25):
                assert f"regressor_{ch}" not in ready_after, f"regressor_{ch} must not be in ready to post!"
                assert f"regressor_{ch}" not in review_after, f"regressor_{ch} must not be in needs review!"

        finally:
            rc.stitch = orig_stitch
            rc._probe = orig_probe

        print("  PASS range merge: full SEO, queue removal & ready-to-post cleanup verified")
    finally:
        ingest.PROJECTS = saved

if __name__ == "__main__":
    test_range_merge()
