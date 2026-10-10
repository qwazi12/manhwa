"""Test multi-chapter compilation merging into 1 long-form video."""
import os
import sys
import json
import tempfile
from fastapi.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest
import server
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

        client = TestClient(server.app)
        res = client.post("/api/ranges/plan", json={"projects": ["regressor_23", "regressor_24", "regressor_25"]})
        assert res.status_code == 200
        data = res.json()
        assert data["chapters"] == [23, 24, 25]
        assert data["ready"] is True

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
        finally:
            rc.stitch = orig_stitch
            rc._probe = orig_probe

        print("  PASS range merge plan and build")
    finally:
        ingest.PROJECTS = saved

if __name__ == "__main__":
    test_range_merge()
