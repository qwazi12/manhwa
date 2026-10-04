"""Script editor's notes (LongForm lesson, step 7): the narration's critique
pass records what it flagged and each rewrite before/after, ingest saves it as
critique.json and 🛡 Check shows it. Model calls faked; no network, no money."""
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
    import narrate
    panels = [{"panel_id": f"p{i}", "visual_description": f"scene {i}", "ocr_text": ""} for i in range(4)]
    saved = {k: getattr(narrate, k) for k in ("generate_global_beatsheet", "narrate_scene", "critique_units", "revise_unit")}
    narrate.generate_global_beatsheet = lambda panels, model: "beats"
    narrate.narrate_scene = lambda scene, *a, **k: "[ID: %s] He walks in." % scene[0]["panel_id"]
    narrate.critique_units = lambda results, *a, **k: [
        {"unit": 0, "type": "hallucination", "problem": "names a sword never shown", "fix": "drop the sword"}]
    narrate.revise_unit = lambda scene, draft, issues, *a, **k: "[ID: %s] He walks in, empty-handed." % scene[0]["panel_id"]
    try:
        script, results = narrate.generate_narration(panels, verbose=False, direct=False)
    finally:
        for k, v in saved.items():
            setattr(narrate, k, v)
    c = narrate.LAST_CRITIQUE
    check("the critique's issues are recorded", c["status"] == "done" and c["issues"][0]["type"] == "hallucination")
    check("each rewrite is kept before/after, without the panel tags",
          c["revised"] and c["revised"][0]["before"] == "He walks in." and "empty-handed" in c["revised"][0]["after"])
    check("...and the rewrite is what the script uses", "empty-handed" in script)

    def boom(*a, **k):
        raise RuntimeError("reviewer down")
    narrate.generate_global_beatsheet = lambda panels, model: "beats"
    narrate.narrate_scene = lambda scene, *a, **k: "[ID: %s] Text." % scene[0]["panel_id"]
    narrate.critique_units = boom
    try:
        narrate.generate_narration(panels, verbose=False, direct=False)
    finally:
        for k, v in saved.items():
            setattr(narrate, k, v)
    check("a failed review is recorded as skipped (never kills the job)",
          narrate.LAST_CRITIQUE["status"].startswith("skipped") and not narrate.LAST_CRITIQUE["revised"])

    import ingest
    import server
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="crit_")
    os.makedirs(os.path.join(root, "ch1"))
    json.dump({"status": "done", "issues": [], "revised": []}, open(os.path.join(root, "ch1", "critique.json"), "w"))
    os.makedirs(os.path.join(root, "ch2"))
    saved_root = ingest.PROJECTS
    ingest.PROJECTS = root
    try:
        cl = TestClient(server.app)
        check("the route returns a chapter's notes", cl.get("/api/critique?project=ch1").json()["status"] == "done")
        check("...or says there are none", cl.get("/api/critique?project=ch2").json() == {"missing": True})
    finally:
        ingest.PROJECTS = saved_root
    src = open(os.path.join(HERE, "ingest.py"), encoding="utf-8").read()
    check("ingest saves critique.json next to the script", '"critique.json"' in src and "LAST_CRITIQUE" in src)
    sb = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    check("🛡 Check shows it", 'id="critbox"' in sb and "loadCritique()" in sb)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
