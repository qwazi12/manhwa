"""Per-chapter pipeline strip (LongForm lesson, step 7): where a chapter
stands, and re-running ONE step clears exactly that step and what was built
from it, then queues the chapter's ingest. Temp projects; ingest faked."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []
URL = "https://asuracomic.net/series/murim-psychopath-1a2b3c4d/chapter/44"


def check(name, ok):
    R.append((name, bool(ok)))


def build(root, pid, engine="gemini"):
    p = os.path.join(root, pid)
    for d in ("pages", "crops", "audio/slices", "clips", "exports"):
        os.makedirs(os.path.join(p, d), exist_ok=True)
    for i in range(3):
        open(os.path.join(p, "pages", f"{i:03d}.jpg"), "wb").write(b"x")
        open(os.path.join(p, "crops", f"p{i}.png"), "wb").write(b"x")
        open(os.path.join(p, "audio", f"beat_{i:03d}.mp3"), "wb").write(b"x")
    open(os.path.join(p, "audio", "slices", "s.mp3"), "wb").write(b"x")
    json.dump([{"panel_id": "p0", "role": "art"}, {"panel_id": "p1", "role": "bubble"}],
              open(os.path.join(p, "descriptions.json"), "w"))
    open(os.path.join(p, "script.txt"), "w").write("He walks in. She waits.")
    for f in ("script.json", "direct_speech.json", "beatsheet.json", "review.json", "storyboard.json", "edits.log.jsonl"):
        open(os.path.join(p, f), "w").write("{}")
    json.dump({"status": "done", "issues": [1], "revised": []}, open(os.path.join(p, "critique.json"), "w"))
    json.dump({"voice": "Charon", "style": "energetic"}, open(os.path.join(p, "tts.json"), "w"))
    json.dump([{"seg_index": 0}, {"seg_index": 1}], open(os.path.join(p, "segments.json"), "w"))
    open(os.path.join(p, "clips", "seg_000.mp4"), "wb").write(b"x")
    json.dump({"url": URL, "engine": engine, "series": "Murim Psychopath", "chapter": "44"},
              open(os.path.join(p, "project.json"), "w"))
    return p


def main():
    import ingest
    import pipeline_steps as ps
    import server
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="pipe_")
    pid = ingest.project_id(URL)
    p = build(root, pid)
    st = {s["key"]: s for s in ps.status(p)}
    check("the strip has every step in order",
          list(st) == ["scrape", "split", "describe", "narrate", "voice", "match", "render", "export"])
    check("counts and details are read from the chapter's files",
          st["scrape"]["detail"] == "3 page(s)" and "1 bubble" in st["describe"]["detail"]
          and st["narrate"]["detail"].startswith("5 words") and "Charon" in st["voice"]["detail"])
    check("half-rendered clips show as partial", st["render"]["state"] == "partial")
    check("no video yet shows as missing", st["export"]["state"] == "missing")
    check("only describe/narrate/voice/match offer a re-run",
          [k for k, s in st.items() if s["rerun"]] == ["describe", "narrate", "voice", "match"])

    q = build(tempfile.mkdtemp(prefix="pipe_"), "x")
    ps.prepare_rerun(q, "match")
    check("re-run timeline: clears the board and clips, keeps script and voice",
          not os.path.exists(os.path.join(q, "segments.json")) and not os.path.exists(os.path.join(q, "storyboard.json"))
          and not os.path.isdir(os.path.join(q, "clips")) and os.path.exists(os.path.join(q, "script.txt"))
          and os.path.exists(os.path.join(q, "audio", "beat_000.mp3")) and os.path.exists(os.path.join(q, "tts.json")))
    q = build(tempfile.mkdtemp(prefix="pipe_"), "x")
    ps.prepare_rerun(q, "voice")
    check("re-run voice: clears the recordings and the voice pin, keeps the script",
          not os.listdir(os.path.join(q, "audio", "slices")) and not os.path.exists(os.path.join(q, "audio", "beat_000.mp3"))
          and not os.path.exists(os.path.join(q, "tts.json")) and os.path.exists(os.path.join(q, "script.txt")))
    q = build(tempfile.mkdtemp(prefix="pipe_"), "x")
    ps.prepare_rerun(q, "narrate")
    check("re-run script: clears script, editor notes and voice; keeps descriptions",
          not os.path.exists(os.path.join(q, "script.txt")) and not os.path.exists(os.path.join(q, "critique.json"))
          and not os.path.exists(os.path.join(q, "audio", "beat_000.mp3")) and os.path.exists(os.path.join(q, "descriptions.json")))
    q = build(tempfile.mkdtemp(prefix="pipe_"), "x")
    ps.prepare_rerun(q, "describe")
    check("re-run describe: clears descriptions and everything after; pages and panels kept",
          not os.path.exists(os.path.join(q, "descriptions.json")) and not os.path.exists(os.path.join(q, "script.txt"))
          and len(os.listdir(os.path.join(q, "crops"))) == 3 and len(os.listdir(os.path.join(q, "pages"))) == 3)
    check("exports are never touched by a re-run", os.path.isdir(os.path.join(q, "exports")))
    try:
        ps.prepare_rerun(q, "export")
        check("unknown step refused", False)
    except ValueError:
        check("unknown step refused", True)

    # routes
    saved = (ingest.PROJECTS, server._enqueue_ingest)
    ingest.PROJECTS = root
    queued = []
    server._enqueue_ingest = lambda url, **k: queued.append((url, k)) or "job1"
    try:
        c = TestClient(server.app)
        v = c.get(f"/api/pipeline?project={pid}").json()
        check("the route returns the strip and says re-run is allowed", v["can_rerun"] and len(v["steps"]) == 8)
        r = c.post("/api/pipeline/rerun", json={"project": pid, "step": "voice"})
        check("re-run queues the chapter's ingest (same URL, same version, not fresh)",
              r.status_code == 200 and queued and queued[0][0] == URL and queued[0][1]["variant"] == ""
              and queued[0][1]["source"] == "rerun" and not queued[0][1].get("fresh"))
        v2 = build(root, pid + "-v2")
        r = c.post("/api/pipeline/rerun", json={"project": pid + "-v2", "step": "match"})
        check("a saved version re-runs as that version", r.status_code == 200 and queued[-1][1]["variant"] == "v2")
        server.INGEST["busy1"] = {"status": "running", "project": pid, "url": URL}
        r = c.post("/api/pipeline/rerun", json={"project": pid, "step": "match"})
        check("refused while the chapter is being made", r.status_code == 409 and "ingest" in r.json()["detail"])
        server.INGEST.pop("busy1")
        server.INGEST["busy2"] = {"status": "queued", "url": URL, "variant": "v2"}
        r = c.post("/api/pipeline/rerun", json={"project": pid, "step": "match"})
        check("a queued SAVED VERSION (same link) doesn't block the original", r.status_code == 200)
        server.INGEST.pop("busy2")
        lab = build(root, "murim-lab-1", engine="claude")
        r = c.post("/api/pipeline/rerun", json={"project": "murim-lab-1", "step": "match"})
        check("Claude-lab chapters can't re-run a step here", r.status_code == 409)
        check("bad step -> 400", c.post("/api/pipeline/rerun", json={"project": pid, "step": "render"}).status_code == 400)
    finally:
        ingest.PROJECTS, server._enqueue_ingest = saved
    sb = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    check("Projects rows have the 🧩 steps strip with a two-tap re-run",
          "pipeOpen('${{p.id}}')" in sb and "pipeRerun(" in sb and "tap again: " in sb)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
