"""Upload recovery (2026-11-13/14). An upload's identity is written down before
its bytes are sent and is only retired when Upload-Post PROVABLY refused it.
Every other outcome — crash, timeout, empty or malformed answer, status check
unavailable — is unknown: the id is kept and the next attempt asks Upload-Post
before sending anything. Channels that already have the video are never sent
it again. Provider behaviour follows docs.upload-post.com (read 2026-11-14).
No network."""
import http.server
import os
import sys
import tempfile
import threading
import time
import tracemalloc

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server  # noqa: E402
import upload_post  # noqa: E402

YT, TT = "mk:youtube", "mk:tiktok"


class Provider:
    """Upload-Post in memory, behaving as documented: remembers request ids,
    returns the existing job for a repeated Idempotency-Key, answers status
    per platform, 404 not_found for an id it never saw."""

    def __init__(self):
        self.jobs = {}                 # rid -> {platform: outcome dict}
        self.sent = []                 # (rid, [platforms]) for every upload that reached it
        self.duplicates = 0            # times a platform got the video twice under different ids
        self.then = None               # what happens AFTER the upload arrived: exception or answer
        self.before = None             # exception raised BEFORE anything is sent
        self.status_down = False
        self.platform_result = {}      # platform -> "ok" | "fail" | "wait" (default ok)
        self.rejected = upload_post.rejected
        self.classify_response = upload_post.classify_response

    def upload_video_post(self, video, md, targets, thumbnail_path=None, on_step=None, request_id=None):
        if self.before is not None:
            e, self.before = self.before, None
            raise e
        nets = [t.partition(":")[2] or t for t in targets]
        if request_id in self.jobs:                    # same Idempotency-Key: the existing job, no new post
            return {"success": True, "request_id": request_id, "message": "existing job"}
        for other in self.jobs.values():
            self.duplicates += sum(1 for n in nets if other.get(n, {}).get("state") == "published")
        self.jobs[request_id] = {n: self._outcome(n) for n in nets}
        self.sent.append((request_id, nets))
        then, self.then = self.then, None
        if isinstance(then, BaseException):
            raise then
        if then is not None:
            return then() if callable(then) else then
        return {"success": True, "request_id": request_id, "total_platforms": len(nets)}

    def _outcome(self, net):
        kind = self.platform_result.get(net, "ok")
        if kind == "ok":
            return {"state": "published", "url": f"https://{net}.example/{len(self.sent)}", "platform_post_id": "id1"}
        if kind == "fail":
            return {"state": "failed", "error": f"{net} refused it"}
        return {"state": "pending"}

    def upload_detail(self, rid):
        if self.status_down:
            return {"state": "unknown", "platforms": {}, "why": "503"}
        if rid not in self.jobs:
            return {"state": "absent", "platforms": {}}
        plats = self.jobs[rid]
        all_failed = plats and all(p["state"] == "failed" for p in plats.values())
        return {"state": "failed" if all_failed else "accepted", "status": "completed", "platforms": dict(plats)}

    def posts(self, net):
        return sum(1 for p in self.jobs.values() if p.get(net, {}).get("state") == "published")


@pytest.fixture
def env(monkeypatch):
    fake, store, pdir = Provider(), {}, tempfile.mkdtemp(prefix="dur_")
    targets = [YT]
    monkeypatch.setattr(server, "_get_publish_backend", lambda: ("upload_post", fake))
    monkeypatch.setattr(server, "publish_eligibility", lambda p, n: {
        "ready": True, "blockers": [],
        "accounts": {"accounts": [{"account_id": YT, "network": "youtube", "username": "Flamingo Remix"},
                                  {"account_id": TT, "network": "tiktok", "username": "Flamingo"}]}})
    monkeypatch.setattr(server, "load_publish", lambda p: {"v.mp4": {"targets": list(targets), "title": "t"}})
    monkeypatch.setattr(server, "publish_defaults", lambda p: {})
    monkeypatch.setattr(server, "load_publishes", lambda p: store)
    monkeypatch.setattr(server, "save_publishes", lambda p, d: store.update(d))
    monkeypatch.setattr(server, "_control_gate", lambda *a, **k: None)
    monkeypatch.setattr(server, "_persist_job", lambda *a, **k: None)
    monkeypatch.setattr(server, "_series_master_thumb", lambda p, t: t)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    n = [0]

    def post(**kw):
        n[0] += 1
        jid = f"job{n[0]}"
        server.JOBS[jid] = {}
        try:
            server._run_publish_job(jid, pdir, "v.mp4", **kw)
        except SystemExit:
            pass                                       # the process died
        return store.get("v.mp4") or {}
    return fake, store, targets, post


def _status(rec):
    return {r["account_id"]: r["status"] for r in rec.get("results") or []}


def test_normal_upload_is_accepted_then_published(env):
    fake, store, _t, post = env
    rec = post()
    assert rec["status"] == "published" and _status(rec) == {YT: "published"}
    att = rec["uploads"]["mk"]
    assert len(att) == 1 and att[0]["outcome"] == "finished" and fake.sent == [(att[0]["id"], ["youtube"])]
    assert rec["results"][0]["url"].startswith("https://")


def test_crash_before_anything_was_sent_resends_under_the_same_id(env):
    fake, store, _t, post = env
    fake.before = SystemExit("killed before the first byte")
    rec = post()
    rid = rec["uploads"]["mk"][0]["id"]                     # the id was on record first
    assert fake.sent == [] and rec["status"] == "in_progress"
    rec = post()
    assert fake.sent == [(rid, ["youtube"])] and rec["status"] == "published"      # same id, one upload


def test_crash_after_acceptance_before_the_result_was_saved_sends_nothing_again(env):
    fake, store, _t, post = env
    fake.then = SystemExit("restarted while waiting for the answer")
    rec = post()
    rid = rec["uploads"]["mk"][0]["id"]
    assert fake.sent == [(rid, ["youtube"])] and not rec.get("results")
    rec = post()
    assert len(fake.sent) == 1 and fake.posts("youtube") == 1
    assert rec["status"] == "published" and rec["results"][0]["request_id"] == rid


@pytest.mark.parametrize("answer", [{}, None, [], "ok", {"message": "?"}, {"success": "true"}, {"request_id": "x"}])
def test_empty_or_malformed_answer_is_unknown_not_a_refusal(env, answer):
    """Reviewed code: `sure_no = not (created or {}).get("success")` took an
    empty or malformed answer for a refusal and deleted the upload id."""
    fake, store, _t, post = env
    fake.then = lambda: answer
    rec = post()
    att = rec["uploads"]["mk"]
    assert len(att) == 1 and att[0]["outcome"] is None                      # id kept
    assert _status(rec) == {YT: "unconfirmed"} and rec["status"] == "failed"
    assert "not known whether" in rec["results"][0]["error"]
    rec = post()                                                            # reconcile: it had arrived
    assert len(fake.sent) == 1 and fake.duplicates == 0 and rec["status"] == "published"


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionResetError("reset"), ValueError("bad JSON"),
                                   upload_post.UploadPostError("Upload failed (502)", 502, "<html>"),
                                   upload_post.UploadPostError("Upload failed (500)", 500, '{"success": false, "error": "x"}'),
                                   upload_post.UploadPostError("Upload failed (400)", 400, "<html>not json</html>"),
                                   upload_post.UploadPostError("Could not reach Upload-Post")])
def test_timeouts_and_server_errors_keep_the_id(env, error):
    fake, store, _t, post = env
    fake.then = error                                       # the upload arrived, the answer did not
    rec = post()
    assert rec["uploads"]["mk"][0]["outcome"] is None and _status(rec) == {YT: "unconfirmed"}
    rec = post()
    assert len(fake.sent) == 1 and rec["status"] == "published"


def test_nothing_is_sent_while_upload_post_cannot_be_asked(env):
    fake, store, _t, post = env
    fake.then = TimeoutError("timed out")
    post()
    fake.status_down = True
    for _ in range(3):
        rec = post()
        assert len(fake.sent) == 1                          # never a second upload
        assert rec["status"] == "failed" and "nothing was sent" in rec["error"]
        assert rec["uploads"]["mk"][0]["outcome"] is None
    fake.status_down = False
    assert post()["status"] == "published" and len(fake.sent) == 1


def test_only_a_documented_refusal_retires_the_id(env):
    fake, store, _t, post = env
    fake.before = upload_post.UploadPostError("Upload failed (429)", 429,
                                              '{"success": false, "message": "This upload would exceed your monthly limit."}')
    rec = post()
    first = rec["uploads"]["mk"][0]
    assert first["outcome"] == "rejected" and _status(rec) == {YT: "failed"} and fake.sent == []
    rec = post()
    ids = [a["id"] for a in rec["uploads"]["mk"]]
    assert len(ids) == 2 and ids[0] != ids[1] and fake.sent == [(ids[1], ["youtube"])]
    assert rec["status"] == "published"


def test_answer_that_says_no_is_a_refusal(env):
    fake, store, _t, post = env
    fake.before = None
    real = fake.upload_video_post
    fake.upload_video_post = lambda *a, **k: {"success": False, "message": "Username required in form data"}
    rec = post()
    assert rec["uploads"]["mk"][0]["outcome"] == "rejected" and rec["results"][0]["error"].startswith("Username")
    fake.upload_video_post = real
    assert post()["status"] == "published"


def test_partial_success_resends_only_the_channel_that_failed(env):
    fake, store, targets, post = env
    targets.append(TT)
    fake.platform_result = {"youtube": "ok", "tiktok": "fail"}
    rec = post()
    assert _status(rec) == {YT: "published", TT: "failed"} and rec["status"] == "partial"
    first = rec["uploads"]["mk"][0]["id"]
    fake.platform_result = {"tiktok": "ok"}
    rec = post()
    assert fake.sent == [(first, ["youtube", "tiktok"]), (rec["uploads"]["mk"][1]["id"], ["tiktok"])]
    assert fake.posts("youtube") == 1 and fake.posts("tiktok") == 1 and fake.duplicates == 0
    assert _status(rec) == {YT: "published", TT: "published"} and rec["status"] == "published"
    assert rec["results"][0]["request_id"] == first                         # YouTube keeps its original post
    again = post()                                                          # pressing Post once more
    assert len(fake.sent) == 2 and again["status"] == "published"


def test_partial_success_then_crash_does_not_duplicate_the_published_channel(env):
    fake, store, targets, post = env
    targets.append(TT)
    fake.platform_result = {"youtube": "ok", "tiktok": "fail"}
    fake.then = SystemExit("died before saving anything")
    post()
    fake.platform_result = {"tiktok": "ok"}
    rec = post()
    assert fake.posts("youtube") == 1 and fake.posts("tiktok") == 1 and fake.duplicates == 0
    assert rec["status"] == "published"


def test_channel_still_processing_is_waited_for_not_resent(env):
    fake, store, _t, post = env
    fake.platform_result = {"youtube": "wait"}
    rec = post()
    assert _status(rec) == {YT: "submitted"} and rec["status"] == "in_progress"
    rec = post()
    assert len(fake.sent) == 1 and _status(rec) == {YT: "submitted"}
    next(iter(fake.jobs.values()))["youtube"] = {"state": "published", "url": "https://youtu.be/x"}
    assert post()["status"] == "published" and len(fake.sent) == 1


def test_success_without_a_tracking_id_is_not_called_published(env):
    fake, store, _t, post = env
    fake.platform_result = {"youtube": "wait"}
    fake.then = lambda: {"success": True}                   # accepted, no request_id in the answer
    rec = post()
    assert _status(rec) == {YT: "submitted"}                # tracked by OUR id; not "published"
    assert rec["results"][0]["request_id"] == rec["uploads"]["mk"][0]["id"]
    assert not rec["results"][0].get("published_at")


def test_scheduled_publication_is_recorded_apart_from_live(env):
    fake, store, _t, post = env
    rec = post(publish_at=time.time() + 3600)
    r = rec["results"][0]
    assert r["status"] == "published" and r["visibility"] == "scheduled" and r["public_at"] > time.time()
    store.clear()
    fake.jobs.clear()
    r2 = post()["results"][0]
    assert r2["status"] == "published" and "visibility" not in r2


def test_synchronous_answer_with_per_platform_results(env):
    fake, store, targets, post = env
    targets.append(TT)
    fake.then = lambda: {"success": True, "results": {"youtube": {"success": True, "url": "https://youtu.be/a"},
                                                      "tiktok": {"success": False, "error": "no account"}}}
    rec = post()
    assert _status(rec) == {YT: "published", TT: "failed"} and rec["results"][1]["error"] == "no account"


def test_accepted_upload_the_provider_later_forgot_is_unconfirmed(env):
    fake, store, _t, post = env
    fake.platform_result = {"youtube": "wait"}
    rec = post()
    fake.jobs.clear()
    out = server._resolve_upload_results(fake, [dict(r) for r in rec["results"]])
    assert out[0]["status"] == "unconfirmed"


def test_orphaned_publish_can_be_started_again_but_a_live_one_cannot():
    now = time.time()
    old = {"status": "in_progress", "job": "gone", "results": [], "updated_at": now - 3600}
    assert server._publish_orphaned(old, now)
    assert not server._publish_orphaned(dict(old, updated_at=now - 30), now)            # too recent
    server.JOBS["live"] = {"status": "running"}
    assert not server._publish_orphaned(dict(old, job="live"), now)                     # worker alive
    assert not server._publish_orphaned(dict(old, results=[{"status": "submitted"}]), now)  # accepted already
    assert not server._publish_orphaned({"status": "published"}, now)


def test_only_a_documented_refusal_counts_as_rejected():
    E = upload_post.UploadPostError
    no = '{"success": false, "message": "x"}'
    for code in (400, 401, 403, 404, 429):
        assert upload_post.rejected(E("x", code, no))
        assert not upload_post.rejected(E("x", code, "<html>gateway page</html>"))
        assert not upload_post.rejected(E("x", code, ""))
        assert not upload_post.rejected(E("x", code, '{"success": true}'))
    for unsure in (E("gateway", 502, no), E("server", 500, no), E("conflict", 409, no), E("slow", 408, no),
                   E("could not reach Upload-Post"), TimeoutError("timed out"), ConnectionResetError(), ValueError()):
        assert not upload_post.rejected(unsure)


def test_classify_response():
    c = upload_post.classify_response
    for bad in (None, {}, [], "", {"message": "x"}, {"success": 1}, {"success": "false"}):
        assert c(bad)[0] == "unknown", bad
    assert c({"success": False, "message": "no"}) == ("rejected", "no")
    assert c({"success": True, "request_id": "r"}) == ("accepted", {"request_id": "r", "job_id": None})
    assert c({"success": True, "job_id": "j"})[1]["job_id"] == "j"
    assert c({"success": True}) == ("accepted", {"request_id": None, "job_id": None})
    kind, per = c({"success": True, "results": {"youtube": {"success": True, "url": "u"}, "x": {"success": False, "error": "e"},
                                                 "threads": {"skipped": True, "skip_reason": "profile_platform_not_configured"}}})
    assert kind == "finished" and per["youtube"]["state"] == "published" and per["x"]["state"] == "failed"
    assert per["threads"]["state"] == "failed"


def test_upload_detail_follows_the_documented_status_answers(monkeypatch):
    E = upload_post.UploadPostError
    cfg = {"api_key": "k"}

    def answer(a):
        def f(rid, cfg=None, _http=None):
            if isinstance(a, BaseException):
                raise a
            return a
        monkeypatch.setattr(upload_post, "upload_status", f)
        return upload_post.upload_detail("r", cfg=cfg)

    nf = '{"request_id": "r", "status": "not_found", "message": "No upload request found with this ID"}'
    assert answer(E("404", 404, nf))["state"] == "absent"                   # the documented answer
    assert answer({"status": "not_found"})["state"] == "absent"
    # anything undocumented is unknown — never "absent"
    for a in (E("404", 404, "<html>Not Found</html>"), E("404", 404, ""), E("404", 404, '{"success": false, "message": "User not found"}'),
              E("gw", 503, ""), E("could not reach"), TimeoutError(), {}, {"status": "weird"}, {"results": []}, [], None):
        assert answer(a)["state"] == "unknown", a
    for st in ("pending", "queued", "processing", "in_progress", "completed"):
        assert answer({"status": st})["state"] == "accepted"
    d = answer({"status": "in_progress", "results": [
        {"platform": "youtube", "success": True, "post_url": "https://youtu.be/a", "profile_username": "mk"},
        {"platform": "tiktok", "success": False, "status": "failed", "message": "refused"},
        {"platform": "x", "status": "retryable", "success": False},
        {"platform": "threads", "status": "queued"}]})
    assert d["state"] == "accepted"
    assert [d["platforms"][k]["state"] for k in ("youtube", "tiktok", "x", "threads")] == ["published", "failed", "pending", "pending"]
    assert answer({"status": "failed", "results": [{"platform": "youtube", "success": False}]})["state"] == "failed"


def test_gateway_error_resends_only_on_the_documented_not_found(monkeypatch, tmp_path):
    """Inside one upload call: after a 502 the status endpoint is asked. A
    resend needs its "not_found"; if it cannot say, the call stops."""
    import io
    import urllib.error
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 32)
    cfg = {"api_key": "k", "user_profile": "mk"}
    sends = []

    def urlopen(req, timeout=None):
        sends.append(req.get_header("Idempotency-key"))
        raise urllib.error.HTTPError("u", 502, "gw", {}, io.BytesIO(b"<html>"))
    monkeypatch.setattr(upload_post.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(upload_post.time, "sleep", lambda s: None)
    monkeypatch.setattr(upload_post, "upload_detail", lambda rid, cfg=None: {"state": "unknown", "platforms": {}})
    with pytest.raises(upload_post.UploadPostError):
        upload_post.upload_video_post(str(video), {"title": "t"}, ["youtube"], cfg=cfg, request_id="rid")
    assert sends == ["rid"]                                 # not sent again while the outcome is unknown
    sends.clear()
    monkeypatch.setattr(upload_post, "upload_detail", lambda rid, cfg=None: {"state": "absent", "platforms": {}})
    with pytest.raises(upload_post.UploadPostError):
        upload_post.upload_video_post(str(video), {"title": "t"}, ["youtube"], cfg=cfg, request_id="rid")
    assert sends == ["rid"] * upload_post.UPLOAD_ATTEMPTS   # resent, always under the same key


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
