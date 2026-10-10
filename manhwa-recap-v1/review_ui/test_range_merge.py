"""Test multi-chapter compilation merging with 'Save as series defaults' inheritance."""
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
import series_pack as spk

def test_range_merge_inherits_series_defaults():
    root = tempfile.mkdtemp()
    saved = ingest.PROJECTS
    try:
        ingest.PROJECTS = root
        series_name = "A Regressors Tale of Cultivation"
        sid = "regressor"

        for ch in (23, 24, 25):
            pid = f"{sid}_{ch}"
            pdir = os.path.join(root, pid)
            os.makedirs(os.path.join(pdir, "exports"), exist_ok=True)
            json.dump({
                "id": pid,
                "series": series_name,
                "series_id": sid,
                "chapter": str(ch),
                "url": f"https://asurascans.com/comics/{sid}/chapter/{ch}"
            }, open(os.path.join(pdir, "project.json"), "w"))
            open(os.path.join(pdir, "exports", "final.mp4"), "wb").write(b"video")
            json.dump({"final.mp4": {"status": "approved"}}, open(os.path.join(pdir, "reviews.json"), "w"))

        # Queue ch 23 and 24
        pq._save(root, {
            "items": [
                {"id": "q1", "project": "regressor_23", "name": "final.mp4", "status": "queued"},
                {"id": "q2", "project": "regressor_24", "name": "final.mp4", "status": "queued"},
                {"id": "q3", "project": "other_1", "name": "final.mp4", "status": "queued"}
            ],
            "slots_done": {}
        })

        client = TestClient(server.app)

        # ⚡ User previously clicked "Save as series defaults" on chapter 23
        defaults_in = server.SeriesDefaultsIn(
            project="regressor_23",
            name="final.mp4",
            metadata={
                "title": f"He Cultivates Forever - {series_name} Chapter 23 Manhwa Recap",
                "description": f"Official recap of {series_name} Chapter 23. Follow the cultivator journey.",
                "tags": ["rtoc", "cultivation master", "top manhwa"],
                "playlist": "PL-cultivation-series",
                "privacy": "unlisted",
                "targets": ["mk:youtube", "mk:tiktok"],
                "category_id": "24",
                "made_for_kids": False,
                "synthetic_disclosure": True
            },
            apply_all=True
        )
        res_def = client.post("/api/publish/series_defaults", json=defaults_in.model_dump())
        assert res_def.status_code == 200, f"Failed to save series defaults: {res_def.text}"

        # Now merge chapters 23, 24, 25 into a long-form video
        orig_stitch = rc.stitch
        orig_probe = rc._probe
        rc.stitch = lambda chs, out: [(23, 0.0), (24, 60.0), (25, 120.0)]
        rc._probe = lambda path: 180.0
        try:
            build_res = client.post("/api/ranges/build", json={"projects": ["regressor_23", "regressor_24", "regressor_25"]})
            assert build_res.status_code == 200
            bdata = build_res.json()
            assert bdata["ok"] is True
            range_pid = bdata["project"]

            # 1. Queue eviction verified
            active_q = [it["id"] for it in pq.load(root)["items"] if it["status"] in pq.ACTIVE]
            assert "q1" not in active_q and "q2" not in active_q
            assert active_q == ["q3"]

            # 2. Reviews marked 'merged'
            for ch in (23, 24, 25):
                rev = json.load(open(os.path.join(root, f"regressor_{ch}", "reviews.json")))
                assert rev["final.mp4"]["status"] == "merged"

            # 3. Check the long-form video's publish.json
            rdir = os.path.join(root, range_pid)
            export_name = bdata["export"]
            import time
            for _ in range(50):
                if os.path.exists(os.path.join(rdir, "publish.json")):
                    break
                time.sleep(0.1)
            pub_store = json.load(open(os.path.join(rdir, "publish.json")))
            pub_md = pub_store[export_name]

            # Series Defaults applied to merged video!
            assert "He Cultivates Forever" in pub_md["title"], f"Title did not inherit series title template! Got: {pub_md['title']}"
            assert "Chapter 23-25" in pub_md["title"], f"Title did not apply chapter range! Got: {pub_md['title']}"
            assert "Follow the cultivator journey" in pub_md["description"], "Description did not inherit series template!"
            assert "Timestamps:" in pub_md["description"], "Description must contain timestamps!"
            assert "0:00 Chapter 23" in pub_md["description"], "Description must contain Chapter 23 timestamp!"
            assert "1:00 Chapter 24" in pub_md["description"], "Description must contain Chapter 24 timestamp!"
            assert "rtoc" in pub_md["tags"], f"Tags did not inherit series tags! Got: {pub_md['tags']}"
            assert pub_md["playlist"] == "PL-cultivation-series", f"Playlist did not match! Got: {pub_md['playlist']}"
            assert pub_md["privacy"] == "unlisted", f"Privacy did not match! Got: {pub_md['privacy']}"
            assert pub_md["targets"] == ["mk:youtube", "mk:tiktok"], f"Targets did not match! Got: {pub_md['targets']}"

        finally:
            rc.stitch = orig_stitch
            rc._probe = orig_probe

        print("  PASS range merge inherits 'Save as series defaults' perfectly!")
    finally:
        ingest.PROJECTS = saved

if __name__ == "__main__":
    test_range_merge_inherits_series_defaults()
