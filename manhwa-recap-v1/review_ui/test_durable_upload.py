"""An upload's identity survives a restart (2026-11-13). Before: the request
id lived only inside the upload call, so a publish cut off mid-upload stayed
"posting" for good and pressing Post again sent the video a second time."""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server  # noqa: E402


class FakeUP:
    """Upload-Post, in memory: remembers the request ids it received."""

    def __init__(self):
        self.received, self.uploads, self.crash_next = set(), [], False

    def accepted(self, rid):
        return rid in self.received

    def upload_video_post(self, video, md, targets, thumbnail_path=None, on_step=None, request_id=None):
        self.uploads.append(request_id)
        self.received.add(request_id)              # the bytes arrived…
        if self.crash_next:
            self.crash_next = False
            raise SystemExit("server restarted")   # …and the process died before the answer
        return {"success": True, "request_id": request_id}

    def upload_status(self, rid):
        return {"status": "completed", "results": [{"profile_username": "mk", "platform": "youtube",
                                                    "success": True, "post_url": "https://youtu.be/x"}]}


def _env(monkeypatch, fake, store):
    monkeypatch.setattr(server, "_get_publish_backend", lambda: ("upload_post", fake))
    monkeypatch.setattr(server, "publish_eligibility", lambda p, n: {
        "ready": True, "blockers": [],
        "accounts": {"accounts": [{"account_id": "mk:youtube", "network": "youtube", "username": "Flamingo Remix"}]}})
    monkeypatch.setattr(server, "load_publish", lambda p: {"v.mp4": {"targets": ["mk:youtube"], "title": "t"}})
    monkeypatch.setattr(server, "publish_defaults", lambda p: {})
    monkeypatch.setattr(server, "load_publishes", lambda p: store)
    monkeypatch.setattr(server, "save_publishes", lambda p, d: store.update(d))
    monkeypatch.setattr(server, "_control_gate", lambda *a, **k: None)
    monkeypatch.setattr(server, "_persist_job", lambda *a, **k: None)
    monkeypatch.setattr(server, "_series_master_thumb", lambda p, t: t)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)


def test_upload_id_is_stored_before_sending_and_reused_after_a_restart(monkeypatch):
    fake, store, pdir = FakeUP(), {}, tempfile.mkdtemp(prefix="dur_")
    _env(monkeypatch, fake, store)
    server.JOBS["d1"] = {}
    fake.crash_next = True
    try:
        server._run_publish_job("d1", pdir, "v.mp4")
    except SystemExit:
        pass
    rid = store["v.mp4"]["upload_ids"]["mk"]
    assert fake.uploads == [rid]                       # stored id == the one sent
    assert store["v.mp4"]["status"] == "in_progress"   # cut off: nothing settled

    # the owner presses Post again after the restart
    server.JOBS["d2"] = {}
    server._run_publish_job("d2", pdir, "v.mp4")
    assert fake.uploads == [rid]                       # NOT sent a second time
    assert store["v.mp4"]["status"] == "published"
    assert store["v.mp4"]["results"][0]["request_id"] == rid


def test_a_refused_upload_gets_a_new_id_next_time(monkeypatch):
    fake, store, pdir = FakeUP(), {}, tempfile.mkdtemp(prefix="dur_")
    _env(monkeypatch, fake, store)
    refuse = lambda *a, request_id=None, **k: {"success": False, "error": "rejected"}
    monkeypatch.setattr(fake, "upload_video_post", refuse)
    server.JOBS["d3"] = {}
    server._run_publish_job("d3", pdir, "v.mp4")
    assert store["v.mp4"]["status"] == "failed" and not store["v.mp4"].get("upload_ids")


def test_orphaned_publish_can_be_started_again_but_a_live_one_cannot():
    now = time.time()
    old = {"status": "in_progress", "job": "gone", "results": [], "updated_at": now - 3600}
    assert server._publish_orphaned(old, now)
    assert not server._publish_orphaned(dict(old, updated_at=now - 30), now)            # too recent
    server.JOBS["live"] = {"status": "running"}
    assert not server._publish_orphaned(dict(old, job="live"), now)                     # worker alive
    assert not server._publish_orphaned(dict(old, results=[{"status": "submitted"}]), now)  # accepted already
    assert not server._publish_orphaned({"status": "published"}, now)


# ---- review 2026-11-13: an uncertain failure must not lose the upload's identity

import http.server  # noqa: E402
import threading  # noqa: E402
import tracemalloc  # noqa: E402

import pytest  # noqa: E402

import upload_post  # noqa: E402


class FlakyUP(FakeUP):
    """Takes the upload, then the answer is lost (timeout)."""

    def __init__(self):
        super().__init__()
        self.state_answers = []

    def rejected(self, exc):
        return upload_post.rejected(exc)

    def upload_state(self, rid):
        if self.state_answers:
            return self.state_answers.pop(0)
        return "accepted" if rid in self.received else "absent"

    def upload_video_post(self, video, md, targets, thumbnail_path=None, on_step=None, request_id=None):
        self.uploads.append(request_id)
        self.received.add(request_id)
        if self.crash_next:
            self.crash_next = False
            raise TimeoutError("the read operation timed out")
        return {"success": True, "request_id": request_id}


def test_timeout_after_the_upload_arrived_keeps_the_id_and_is_not_sent_twice(monkeypatch):
    fake, store, pdir = FlakyUP(), {}, tempfile.mkdtemp(prefix="dur_")
    _env(monkeypatch, fake, store)
    fake.crash_next = True
    server.JOBS["u1"] = {}
    server._run_publish_job("u1", pdir, "v.mp4")
    rid = store["v.mp4"]["upload_ids"]["mk"]                 # kept, although the attempt failed
    assert store["v.mp4"]["status"] == "failed"
    assert "not known whether" in store["v.mp4"]["results"][0]["error"]

    server.JOBS["u2"] = {}
    server._run_publish_job("u2", pdir, "v.mp4")
    assert fake.uploads == [rid]                             # one upload, ever
    assert store["v.mp4"]["status"] == "published"


def test_nothing_is_sent_while_upload_post_cannot_be_asked(monkeypatch):
    fake, store, pdir = FlakyUP(), {"v.mp4": {"upload_ids": {"mk": "old-id"}}}, tempfile.mkdtemp(prefix="dur_")
    _env(monkeypatch, fake, store)
    fake.state_answers = ["unknown"]
    server.JOBS["u3"] = {}
    server._run_publish_job("u3", pdir, "v.mp4")
    assert fake.uploads == []
    assert store["v.mp4"]["status"] == "failed" and store["v.mp4"]["upload_ids"] == {"mk": "old-id"}
    assert "nothing was sent" in store["v.mp4"]["error"]


def test_absent_upload_is_resent_under_the_same_id(monkeypatch):
    fake, store, pdir = FlakyUP(), {"v.mp4": {"upload_ids": {"mk": "old-id"}}}, tempfile.mkdtemp(prefix="dur_")
    _env(monkeypatch, fake, store)
    server.JOBS["u4"] = {}
    server._run_publish_job("u4", pdir, "v.mp4")
    assert fake.uploads == ["old-id"]                        # same id == same Idempotency-Key


def test_upload_every_channel_refused_gets_a_new_id(monkeypatch):
    fake, store, pdir = FlakyUP(), {"v.mp4": {"upload_ids": {"mk": "old-id"}}}, tempfile.mkdtemp(prefix="dur_")
    _env(monkeypatch, fake, store)
    fake.state_answers = ["failed"]
    server.JOBS["u5"] = {}
    server._run_publish_job("u5", pdir, "v.mp4")
    assert len(fake.uploads) == 1 and fake.uploads[0] != "old-id"


def test_only_a_proven_refusal_counts_as_rejected():
    E = upload_post.UploadPostError
    assert upload_post.rejected(E("bad request", 400)) and upload_post.rejected(E("too large", 413))
    for unsure in (E("gateway", 502), E("server", 500), E("conflict", 409), E("slow down", 429),
                   E("could not reach Upload-Post"), TimeoutError("timed out"), ConnectionResetError()):
        assert not upload_post.rejected(unsure)


def test_upload_state_tells_absent_from_unknown(monkeypatch):
    def status(answer):
        def f(rid, cfg=None, _http=None):
            if isinstance(answer, Exception):
                raise answer
            return answer
        return f
    cfg = {"api_key": "k"}
    for answer, want in ((upload_post.UploadPostError("nf", 404), "absent"),
                         ({"status": "not_found"}, "absent"), ({}, "absent"),
                         (upload_post.UploadPostError("gw", 503), "unknown"),
                         (upload_post.UploadPostError("could not reach"), "unknown"),
                         (TimeoutError(), "unknown"),
                         ({"status": "pending"}, "accepted"),
                         ({"status": "completed", "results": [{"success": True}]}, "accepted"),
                         ({"status": "completed", "results": [{"success": False}]}, "failed")):
        monkeypatch.setattr(upload_post, "upload_status", status(answer))
        assert upload_post.upload_state("r", cfg=cfg) == want, answer


def test_video_is_streamed_from_disk_not_held_in_memory(monkeypatch):
    """A 48 MB file goes out with an exact Content-Length while the process
    holds only a few MB (before: the file twice over)."""
    size = 48 * 1024 * 1024
    d = tempfile.mkdtemp(prefix="up_")
    video = os.path.join(d, "v.mp4")
    with open(video, "wb") as f:
        f.seek(size - 1)
        f.write(b"\0")
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers["Content-Length"])
            got.update(length=n, te=self.headers.get("Transfer-Encoding"), key=self.headers.get("Idempotency-Key"))
            left, head = n, b""
            while left:
                buf = self.rfile.read(min(1 << 20, left))
                head = head or buf[:400]
                left -= len(buf)
            got["head"] = head
            body = b'{"success": true, "request_id": "rid-1"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(upload_post, "API_BASE", f"http://127.0.0.1:{srv.server_port}")
    try:
        tracemalloc.start()
        res = upload_post.upload_video_post(video, {"title": "t"}, ["mk:youtube"],
                                            cfg={"api_key": "k", "user_profile": "mk"}, request_id="rid-1")
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        srv.shutdown()
    assert res["success"] and got["key"] == "rid-1" and got["te"] is None
    assert got["length"] > size and b'name="user"' in got["head"]
    assert peak < 16 * 1024 * 1024, f"peak {peak / 1e6:.0f} MB for a {size / 1e6:.0f} MB file"
