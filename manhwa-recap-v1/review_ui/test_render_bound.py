"""Edit another chapter while one renders (owner, 2026-10-13): a render stays
on its own chapter even when the owner opens another one."""
import json
import os
import threading
import time

import ingest
import server


def _chapter(root, pid, n=5):
    d = os.path.join(root, pid)
    os.makedirs(os.path.join(d, "audio"), exist_ok=True)
    with open(os.path.join(d, "segments.json"), "w") as f:
        json.dump([{"seg_index": i, "beats": [], "clip": f"clips/seg_{i:03d}.mp4", "user_included": True}
                   for i in range(n)], f)


def _setup(tmp_path, monkeypatch):
    root = str(tmp_path / "projects")
    os.makedirs(root)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "WORK", str(tmp_path / "work"))
    os.makedirs(str(tmp_path / "work"))
    _chapter(root, "alpha_1")
    _chapter(root, "beta_1")
    seen = []
    def fake_rerender(seg, wd=None):
        time.sleep(0.12)
        seen.append((server.get_active_project_id(), os.path.basename(server._audio_dir().rstrip("/audio")),
                     seg["seg_index"]))
    monkeypatch.setattr(server, "_rerender", fake_rerender)
    monkeypatch.setattr(server, "_gate_timeline", lambda *a: None)
    monkeypatch.setattr(server, "_revoice_if_outdated", lambda *a: 0)
    monkeypatch.setattr(server, "_clip_parallel", lambda: 2)
    monkeypatch.setattr(server, "_do_export", lambda speed: {"output": f"{server.get_active_project_id()}.mp4"})
    monkeypatch.setattr(server, "_after_export", lambda *a: None)
    monkeypatch.setattr(server, "needs_render", lambda t, p=None: [s["seg_index"] for s in t])
    return root, seen


def _open(tmp_path, pid):
    with open(str(tmp_path / "work" / "active_project.txt"), "w") as f:
        f.write(pid)


def test_render_stays_on_its_chapter_while_the_owner_opens_another(tmp_path, monkeypatch):
    root, seen = _setup(tmp_path, monkeypatch)
    _open(tmp_path, "alpha_1")
    server.JOBS["fa"] = {"type": "finalize", "project": "alpha_1", "status": "queued", "keep_voice": True}
    th = threading.Thread(target=server._run_finalize_job, args=("fa",), daemon=True)
    th.start()
    time.sleep(0.15)
    server.activate_project(server.ActivateIn(id="beta_1"))      # no 409 any more
    assert server.get_active_project_id() == "beta_1"
    th.join(10)
    j = server.JOBS["fa"]
    assert j["status"] == "done" and j["export"] == "alpha_1.mp4"
    assert {p for p, _a, _i in seen} == {"alpha_1"} and {a for _p, a, _i in seen} == {"alpha_1"}
    assert server.get_active_project_id() == "beta_1"           # the owner's chapter is untouched


def test_an_edit_on_the_other_chapter_waits_then_renders_there(tmp_path, monkeypatch):
    root, seen = _setup(tmp_path, monkeypatch)
    _open(tmp_path, "alpha_1")
    server.JOBS["fa2"] = {"type": "finalize", "project": "alpha_1", "status": "queued", "keep_voice": True}
    th = threading.Thread(target=server._run_finalize_job, args=("fa2",), daemon=True)
    th.start()
    time.sleep(0.1)
    _open(tmp_path, "beta_1")
    j1 = server._start_render([1])
    j2 = server._start_render([3])            # a second edit joins the waiting job, no 409
    assert j1 == j2 and server.JOBS[j1]["status"] == "queued"
    th.join(10)
    for _ in range(100):
        if server.JOBS[j1]["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.05)
    assert server.JOBS[j1]["status"] == "done", server.JOBS[j1]
    beta = [(p, i) for p, _a, i in seen if p == "beta_1"]
    assert sorted(i for _p, i in beta) == [1, 3]
    first_beta = next(n for n, (p, _a, _i) in enumerate(seen) if p == "beta_1")
    assert all(p == "alpha_1" for p, _a, _i in seen[:first_beta]) and len(seen[:first_beta]) == 5   # waited
