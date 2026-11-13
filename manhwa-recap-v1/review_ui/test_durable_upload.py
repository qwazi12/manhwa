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
