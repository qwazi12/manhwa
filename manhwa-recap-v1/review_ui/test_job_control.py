"""Operator control tests (Session 27): pause, stop, delete, queue.

Jobs are cooperative, not preemptive — a worker only notices pause/stop when it
next reports progress. These tests pin that contract, and the rule that a job
still doing work cannot be deleted out from under itself.

Run: python3 test_job_control.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import server as srv


def main():
    r = []
    srv._PAUSE_POLL = 0.02              # keep the pause test quick
    noop = lambda _jid: None

    # ---- stop raises inside the worker
    srv.INGEST.clear(); srv.JOBS.clear()
    srv.INGEST["a"] = {"status": "running", "control": "stop"}
    try:
        srv._control_gate(srv.INGEST, "a", noop); raised = False
    except srv.JobCancelled:
        raised = True
    r.append(("a stopped job raises inside the worker", raised))

    # ---- pause blocks, then resume releases
    srv.INGEST["b"] = {"status": "running", "control": "pause"}
    released = []
    def waiter():
        try:
            srv._control_gate(srv.INGEST, "b", noop); released.append("resumed")
        except srv.JobCancelled:
            released.append("stopped")
    t = threading.Thread(target=waiter); t.start()
    time.sleep(0.15)
    r.append(("a paused job reports paused while it waits",
              srv.INGEST["b"]["status"] == "paused" and not released))
    srv.INGEST["b"]["control"] = "run"
    t.join(timeout=3)
    r.append(("resume releases it and restores running",
              released == ["resumed"] and srv.INGEST["b"]["status"] == "running"))

    # ---- pause then stop escapes the wait rather than hanging
    srv.INGEST["c"] = {"status": "running", "control": "pause"}
    out = []
    def waiter2():
        try:
            srv._control_gate(srv.INGEST, "c", noop); out.append("resumed")
        except srv.JobCancelled:
            out.append("stopped")
    t2 = threading.Thread(target=waiter2); t2.start()
    time.sleep(0.1)
    srv.INGEST["c"]["control"] = "stop"
    t2.join(timeout=3)
    r.append(("stopping a PAUSED job still ends it", out == ["stopped"]))

    # ---- a clean job passes straight through
    srv.INGEST["d"] = {"status": "running", "control": "run"}
    srv._control_gate(srv.INGEST, "d", noop)
    r.append(("an uncontrolled job is not delayed", srv.INGEST["d"]["status"] == "running"))

    # ---- the control endpoint
    srv.INGEST["e"] = {"status": "running", "control": "run"}
    srv.job_control(srv.JobControlIn(job_id="e", action="pause"))
    r.append(("pause sets the flag and marks it pausing",
              srv.INGEST["e"]["control"] == "pause"))
    srv.job_control(srv.JobControlIn(job_id="e", action="resume"))
    r.append(("resume clears the flag", srv.INGEST["e"]["control"] == "run"))
    srv.job_control(srv.JobControlIn(job_id="e", action="stop"))
    r.append(("stop sets the stop flag", srv.INGEST["e"]["control"] == "stop"))

    srv.INGEST["f"] = {"status": "done", "control": "run"}
    try:
        srv.job_control(srv.JobControlIn(job_id="f", action="stop")); code = None
    except Exception as ex:
        code = getattr(ex, "status_code", None)
    r.append(("a finished job cannot be stopped (409)", code == 409))
    try:
        srv.job_control(srv.JobControlIn(job_id="nope", action="stop")); code = None
    except Exception as ex:
        code = getattr(ex, "status_code", None)
    r.append(("an unknown job id is a 404", code == 404))
    try:
        srv.job_control(srv.JobControlIn(job_id="e", action="explode")); code = None
    except Exception as ex:
        code = getattr(ex, "status_code", None)
    r.append(("an unknown action is rejected (400)", code == 400))

    # ---- delete: finished go, live ones are protected
    srv.INGEST.clear(); srv.JOBS.clear()
    srv.INGEST["g"] = {"status": "done"}
    srv.INGEST["h"] = {"status": "running"}
    srv.JOBS["i"] = {"status": "error"}
    res = srv.jobs_delete(srv.JobDelIn(job_ids=["g", "h", "i"]))
    r.append(("finished and errored records are deleted",
              set(res["deleted"]) == {"g", "i"}))
    r.append(("a running job is protected, with a reason",
              [x["id"] for x in res["skipped"]] == ["h"]
              and "stop it first" in res["skipped"][0]["reason"]))
    r.append(("...and it survives in the store", "h" in srv.INGEST))

    # ---- queue runs one at a time
    srv.INGEST.clear(); srv._QUEUE.clear(); srv._QUEUE_RUNNING = False
    order, running = [], []
    engines = {}
    variants = {}
    def fake_run(job_id, url, fresh=False, engine="gemini", variant="", direct=None):
        engines[url] = engine
        variants[url] = (variant, direct)
        running.append(job_id)
        r.append(("only one queued ingest runs at a time", len(running) == 1)) if len(running) > 1 else None
        time.sleep(0.05); order.append(url); running.remove(job_id)
        srv.INGEST[job_id]["status"] = "done"
    srv._run_ingest_job = fake_run
    srv._persist_ingest = noop
    ids = [srv._enqueue_ingest(f"http://x/chapter/{n}", engine=e,
                               variant=("v2" if n == 3 else ""),
                               direct=(True if n == 3 else None))
           for n, e in ((1, "gemini"), (2, "claude"), (3, "gemini"))]
    for _ in range(200):
        if all(srv.INGEST[i].get("status") == "done" for i in ids): break
        time.sleep(0.02)
    r.append(("every queued chapter runs", len(order) == 3))
    r.append(("...in the order they were queued",
              order == ["http://x/chapter/1", "http://x/chapter/2", "http://x/chapter/3"]))
    r.append(("a queued ingest keeps its version and direct-speech choice",
              variants.get("http://x/chapter/3") == ("v2", True)
              and variants.get("http://x/chapter/1") == ("", None)))
    r.append(("a queued ingest keeps its engine (it used to be dropped -> Gemini)",
              engines == {"http://x/chapter/1": "gemini", "http://x/chapter/2": "claude",
                          "http://x/chapter/3": "gemini"}))

    # ---- Tracker ingest: the engine picked in the chapter list reaches the
    # pipeline, is validated like the manual drawer, and defaults to Gemini.
    import watchlist as _wl
    saved_wl = (_wl.load, _wl.find, _wl.chapter_url, srv._enqueue_ingest,
                srv._active_ingest_for_url, srv._validator.api_key)
    queued = []
    try:
        _wl.load = lambda root: {}
        _wl.find = lambda data, sid: {"title": "T"}
        _wl.chapter_url = lambda s, key, ch: f"http://x/chapter/{ch}"
        srv._active_ingest_for_url = lambda url, variant="": None
        srv._enqueue_ingest = lambda url, fresh=False, engine="gemini", variant="", direct=None: \
            queued.append((url, engine)) or "job1"
        srv._validator.api_key = lambda: "k"
        srv.api_watchlist_ingest(srv.WLIngestIn(series_id="s", series_key="k", chapter="7"))
        srv.api_watchlist_ingest(srv.WLIngestIn(series_id="s", series_key="k", chapter="8",
                                                engine="claude"))
        r.append(("tracker ingest defaults to Gemini and passes Claude through",
                  queued == [("http://x/chapter/7", "gemini"), ("http://x/chapter/8", "claude")]))
        try:
            srv.api_watchlist_ingest(srv.WLIngestIn(series_id="s", series_key="k",
                                                    chapter="9", engine="gpt"))
            bad = False
        except srv.HTTPException as e:
            bad = e.status_code == 400
        r.append(("...an unknown engine is refused before anything is queued",
                  bad and len(queued) == 2))
        srv._validator.api_key = lambda: ""
        try:
            srv.api_watchlist_ingest(srv.WLIngestIn(series_id="s", series_key="k",
                                                    chapter="10", engine="claude"))
            nokey = False
        except srv.HTTPException as e:
            nokey = "no Claude API key" in e.detail
        r.append(("...and Claude with no key is refused up front, even when queued",
                  nokey and len(queued) == 2))
    finally:
        (_wl.load, _wl.find, _wl.chapter_url, srv._enqueue_ingest,
         srv._active_ingest_for_url, srv._validator.api_key) = saved_wl

    # ---- saved versions ("ch.44 v2") and the per-ingest direct-speech switch
    import ingest as _ingm
    u44 = "https://asurascans.com/comics/murim-psychopath-05c7df14/chapter/44"
    r.append(("a version is its own project folder beside the original",
              _ingm.project_id(u44, "v2") == _ingm.project_id(u44) + "-v2"))
    r.append(("the projects list labels the version distinctly",
              _ingm._derive_series_chapter("x", {"url": u44, "variant": "v2"})[1] == "44 (v2)"
              and _ingm._derive_series_chapter("x", {"url": u44})[1] == "44"))
    for bad in ("V2!", "../x", "a" * 20):
        try:
            srv.start_ingest(srv.IngestIn(url=u44, variant=bad)); refused = False
        except srv.HTTPException as e:
            refused = e.status_code == 400
        r.append((f"a bad version name is refused up front ({bad[:8]!r})", refused))
    try:
        srv.start_ingest(srv.IngestIn(url=u44, variant="v2", engine="claude")); refused = False
    except srv.HTTPException as e:
        refused = "Gemini-only" in e.detail
    r.append(("a Claude ingest with a version is refused, not silently unversioned", refused))
    srv.INGEST.clear()
    srv.INGEST["orig"] = {"status": "running", "url": u44, "variant": ""}
    r.append(("the original running does NOT block starting its v2",
              srv._active_ingest_for_url(u44, "v2") is None
              and srv._active_ingest_for_url(u44) == "orig"))
    srv.INGEST.clear()

    # ---- the controls must actually EXIST in the UI, not just as endpoints
    # A stop that is only reachable from the Logs tab is not a stop button on
    # the thing you are watching, which is what was asked for.
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    _sys.path.insert(0, _os.path.abspath(_os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)), "..")))
    import storyboard as _sb, matcher as _m
    _projs = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "projects")
    _cand = [d for d in sorted(_os.listdir(_projs))
             if _os.path.isdir(_os.path.join(_projs, d)) and not d.startswith("_")]
    if _cand:
        _html = _sb.build_storyboard_html(_os.path.join(_projs, _cand[0]), _m,
                                          review={}, usage_summary={}, approved=False)
        r.append(("the ingest drawer has a Stop control",
                  "stopIngest" in _html and "Stop ingest" in _html))
        r.append(("...and it stops the ACTIVE ingest job",
                  "action: 'stop'" in _html and "activeJob()" in _html))
        r.append(("the render/export strip has a Stop control",
                  "stopRender" in _html and 'id="renderstop"' in _html))
        r.append(("...targeting the finalize job being watched",
                  "finalizeJob" in _html.split("function stopRender")[1][:400]))
        r.append(("the tracker has a per-series remove control",
                  "untrackSeries" in _html and "remove" in _html))
        r.append(("...that says data is NOT deleted, because it is not",
                  "NOT deleted" in _html))
        r.append(("...and offers a way to track it again",
                  "track again" in _html))
        r.append(("every stop confirms first rather than acting instantly",
                  _html.count("confirm(") >= 3))
        r.append(("the Tracker chapter list offers the Gemini/Claude picker",
                  'id="wleng_' in _html and '<option value="claude"' in _html))
        r.append(("...and the tracker ingest sends the picked engine",
                  "queue: true, engine}" in _html))

    # ---- render jobs refuse a broken timeline BEFORE rendering anything.
    # They used to call _rerender straight away and die on the renderer's own
    # check at the first bad clip ("seg 5: beat 4 ... does not fit").
    rendered = []
    saved = {k: getattr(srv, k) for k in ("_gate_timeline", "_rerender",
             "load_segments", "video_segments", "needs_render",
             "active_project_dir", "_persist_job", "_do_export")}
    def bad_gate(segs, action):
        raise srv.HTTPException(400, f"{action} blocked — 1 timing error(s) "
                                     "would cut narration. seg 5: G2")
    try:
        srv._gate_timeline = bad_gate
        srv._rerender = lambda seg, workdir=None: rendered.append(seg["seg_index"])
        srv._persist_job = noop
        srv.load_segments = lambda: [{"seg_index": 5, "user_included": True}]
        srv.video_segments = lambda segs: segs
        srv.needs_render = lambda ticked, pdir: [5]
        srv.active_project_dir = lambda: "/nonexistent"
        srv._do_export = lambda: {}
        srv.JOBS["rj"] = {"status": "queued", "done": 0}
        srv._run_render_job("rj", [5])
        r.append(("a render job refuses a broken timeline before rendering",
                  srv.JOBS["rj"]["status"] == "error" and not rendered))
        r.append(("...and reports the gate's message, not an HTTP repr",
                  srv.JOBS["rj"]["error"].startswith("render blocked")))
        srv.JOBS["fj"] = {"status": "queued"}
        srv._run_finalize_job("fj")
        r.append(("the approve/finalize job refuses it too, before any clip",
                  srv.JOBS["fj"]["status"] == "error" and not rendered and
                  "render blocked" in srv.JOBS["fj"]["error"]))
    finally:
        for k, v in saved.items():
            setattr(srv, k, v)

    # ---- the gate is re-checked PER CLIP, and the loop never writes back.
    # Live 358-lab-claude: the up-front gate passed, the board was edited
    # during a 16-minute render, and seg 66 failed at clip 70/82 inside the
    # renderer. The loop also wrote back the copy it read before each ~13s
    # render, reverting any edit saved meanwhile.
    rendered, writes = [], []
    saved = {k: getattr(srv, k) for k in ("_gate_timeline", "_rerender",
             "load_segments", "video_segments", "needs_render",
             "active_project_dir", "_persist_job", "_do_export",
             "_write_segments")}
    def gate_breaks_at_seg_2(segs, action):
        if segs == [2]:        # the timeline "was edited" before clip 2's turn
            raise srv.HTTPException(400, f"{action} blocked — 1 timing error(s) "
                                         "would cut narration. seg 2: G2 "
                                         "... repair_slices")
    try:
        srv._gate_timeline = gate_breaks_at_seg_2
        srv._rerender = lambda seg, workdir=None: rendered.append(seg["seg_index"])
        srv._write_segments = lambda segs: writes.append(1)
        srv._persist_job = noop
        srv.load_segments = lambda: [{"seg_index": i, "user_included": True}
                                     for i in range(4)]
        srv.video_segments = lambda segs: segs
        srv.needs_render = lambda ticked, pdir: [0, 1, 2, 3]
        srv.active_project_dir = lambda: "/nonexistent"
        srv._do_export = lambda: {}
        srv.JOBS["pc"] = {"status": "queued"}
        srv._run_finalize_job("pc")
        r.append(("a timeline broken mid-render stops at THAT clip, not in the renderer",
                  srv.JOBS["pc"]["status"] == "error" and rendered == [0, 1] and
                  "render blocked" in srv.JOBS["pc"]["error"]))
        r.append(("the finalize loop never writes a stale copy back over edits",
                  writes == []))
        rendered.clear()
        srv.JOBS["pr"] = {"status": "queued", "done": 0}
        srv._run_render_job("pr", [0, 1, 2, 3])
        r.append(("the render job re-checks per clip and never writes back",
                  rendered == [0, 1] and writes == [] and
                  srv.JOBS["pr"]["status"] == "error"))
    finally:
        for k, v in saved.items():
            setattr(srv, k, v)

    # ---- option 1: clips render N at a time, and the scheduler keeps every
    # between-clip guarantee (gate per clip, stop, first failure wins)
    import threading as _th
    saved = {k: getattr(srv, k) for k in ("_gate_timeline", "_rerender",
             "load_segments", "active_project_dir", "_clip_parallel")}
    live, peak, order, lock = [0], [0], [], _th.Lock()
    def slow_render(seg, workdir=None):
        with lock:
            live[0] += 1; peak[0] = max(peak[0], live[0]); order.append(seg["seg_index"])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        if seg["seg_index"] == 5:
            raise RuntimeError("seg 5 render failed")
    try:
        srv._gate_timeline = lambda segs, action: None
        srv._rerender = slow_render
        srv.load_segments = lambda: [{"seg_index": i} for i in range(8)]
        srv.active_project_dir = lambda: "/nonexistent"
        srv._clip_parallel = lambda: 3
        done = []
        srv._render_clips([0, 1, 2, 3], lambda si: None, done.append)
        r.append(("clips render in parallel, never more than the limit",
                  peak[0] == 3 and sorted(done) == [0, 1, 2, 3]))
        r.append(("...and are started in timeline order", order == [0, 1, 2, 3]))
        peak[0] = 0; order.clear(); done = []
        try:
            srv._render_clips([4, 5, 6, 7], lambda si: None, done.append); err = ""
        except RuntimeError as e:
            err = str(e)
        r.append(("a failed clip is reported, and the others in flight still finish",
                  err == "seg 5 render failed" and 4 in done and 5 not in done))
        stops = [0]
        def stop_after_two():
            stops[0] += 1
            if stops[0] > 2:
                raise srv.JobCancelled("stopped by operator")
        order.clear(); done = []
        try:
            srv._render_clips([0, 1, 2, 3], lambda si: None, done.append,
                              control=stop_after_two); cancelled = False
        except srv.JobCancelled:
            cancelled = True
        r.append(("stop is honoured before handing out the next clip",
                  cancelled and order == [0, 1] and sorted(done) == [0, 1]))
        srv._clip_parallel = saved["_clip_parallel"]
        os.environ["RENDER_CLIP_PARALLEL"] = "1"
        r.append(("RENDER_CLIP_PARALLEL=1 restores one-at-a-time (rollback)",
                  srv._clip_parallel() == 1))
        os.environ["RENDER_CLIP_PARALLEL"] = "junk"
        r.append(("a bad RENDER_CLIP_PARALLEL falls back to the default",
                  srv._clip_parallel() == 3))
        os.environ.pop("RENDER_CLIP_PARALLEL", None)
    finally:
        for k, v in saved.items():
            setattr(srv, k, v)

    # ---- repair_slices reports 0 on a healthy timeline (it used to add the
    # 5 keys of repair_slice_binding's summary dict, so never showed "nothing")
    import json, tempfile
    pdir = tempfile.mkdtemp(prefix="repair_total_")
    json.dump([{"seg_index": 0, "start": 0.0, "end": 2.0, "dur": 2.0,
                "silent_hold": True, "beats": []}],
              open(os.path.join(pdir, "segments.json"), "w"))
    saved_apd = srv.active_project_dir
    try:
        srv.active_project_dir = lambda: pdir
        res = srv.sb_repair_slices(srv.RepairIn(dry_run=True))
        r.append(("repair_slices totals 0 on a healthy timeline", res["total"] == 0))
    finally:
        srv.active_project_dir = saved_apd

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
