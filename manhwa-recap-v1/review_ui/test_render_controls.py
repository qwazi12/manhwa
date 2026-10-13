"""⏸ / ▶ / ■ on an Approve → render job actually work (owner, 2026-10-13)."""
import threading
import time

import server


def _setup(monkeypatch, n=6, clip_s=0.15):
    done = []
    monkeypatch.setattr(server, "active_project_dir", lambda: "/tmp/x")
    monkeypatch.setattr(server, "load_segments", lambda: [{"seg_index": i} for i in range(n)])
    monkeypatch.setattr(server, "video_segments", lambda segs: segs)
    monkeypatch.setattr(server, "needs_render", lambda t, p: [s["seg_index"] for s in t])
    monkeypatch.setattr(server, "_gate_timeline", lambda *a: None)
    monkeypatch.setattr(server, "_revoice_if_outdated", lambda *a: 0)
    monkeypatch.setattr(server, "_clip_parallel", lambda: 1)
    monkeypatch.setattr(server, "_rerender", lambda seg, wd=None: (time.sleep(clip_s), done.append(seg["seg_index"])))
    monkeypatch.setattr(server, "_do_export", lambda speed: {"output": "v.mp4"})
    monkeypatch.setattr(server, "_after_export", lambda *a: None)
    monkeypatch.setattr(server, "_PAUSE_POLL", 0.05)
    return done


def _start(job_id="t1"):
    server.JOBS[job_id] = {"type": "finalize", "project": "p", "status": "queued", "keep_voice": True}
    th = threading.Thread(target=server._do_run_finalize_job, args=(job_id,), daemon=True)
    th.start()
    return server.JOBS[job_id], th


def test_stop_halts_the_render_and_skips_the_export(monkeypatch):
    done = _setup(monkeypatch)
    j, th = _start("stop1")
    time.sleep(0.2)
    server.job_control(server.JobControlIn(job_id="stop1", action="stop"))
    th.join(5)
    assert j["status"] == "cancelled" and len(done) < 6 and not j.get("export")


def test_pause_holds_and_resume_continues(monkeypatch):
    done = _setup(monkeypatch)
    j, th = _start("pause1")
    time.sleep(0.2)
    server.job_control(server.JobControlIn(job_id="pause1", action="pause"))
    time.sleep(0.5)
    assert j["status"] == "paused"
    held = len(done)
    time.sleep(0.4)
    assert len(done) == held                       # nothing new while paused
    server.job_control(server.JobControlIn(job_id="pause1", action="resume"))
    th.join(5)
    assert j["status"] == "done" and len(done) == 6 and j["export"] == "v.mp4"
