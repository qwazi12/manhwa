"""Gateway-error recovery for uploads (owner, 2026-10-31). No network."""
import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upload_post as up


class Resp:
    def __init__(self, body): self.body = body
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def gw(code):
    return urllib.error.HTTPError("u", code, "gw", {}, io.BytesIO(b"<html>Gateway</html>"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0" * 64)
    cfg = {"api_key": "offline-placeholder", "user_profile": "mk"}
    monkeypatch.setattr(up, "config", lambda: cfg)
    sleeps, sent, status = [], [], {}
    monkeypatch.setattr(up.time, "sleep", lambda s: sleeps.append(s))

    def run(outcomes):
        it = iter(outcomes)

        def urlopen(req, timeout=None):
            sent.append(req)
            o = next(it)
            if isinstance(o, BaseException):
                raise o
            return Resp(o)
        monkeypatch.setattr(up.urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(up, "upload_status", lambda rid, cfg=None: status.get(rid) or
                            (_ for _ in ()).throw(up.UploadPostError("not found (404)", 404)))
        return up.upload_video_post(str(video), {"title": "T"}, ["youtube"], cfg=cfg)
    return run, sent, sleeps, status


def test_each_upload_sends_its_own_request_id_as_idempotency_key(env):
    run, sent, _, _ = env
    assert run([b'{"success": true, "request_id": "x"}'])["success"]
    key = sent[0].get_header("Idempotency-key")
    # the body is a stream read from disk now (2026-11-13), not bytes in memory
    assert key and f'name="request_id"\r\n\r\n{key}'.encode() in sent[0].data.read()


def test_gateway_error_unknown_upstream_is_resent_with_same_key(env):
    run, sent, sleeps, _ = env
    res = run([gw(504), gw(502), b'{"success": true, "request_id": "r"}'])
    assert res["success"] and len(sent) == 3 and len(sleeps) == 2
    assert len({r.get_header("Idempotency-key") for r in sent}) == 1


def test_gateway_error_already_accepted_is_not_resent(env, monkeypatch):
    run, sent, sleeps, status = env
    real = up.uuid.uuid4
    monkeypatch.setattr(up.uuid, "uuid4", lambda: type("U", (), {"hex": "fixedid"})())
    status["fixedid"] = {"status": "processing"}
    res = run([gw(504)])
    assert res == {"success": True, "request_id": "fixedid", "recovered_after": res["recovered_after"]}
    assert len(sent) == 1 and sleeps == []


def test_gives_up_after_three_attempts(env):
    run, sent, _, _ = env
    with pytest.raises(up.UploadPostError, match="502"):
        run([gw(504), gw(504), gw(502)])
    assert len(sent) == 3


def test_other_errors_are_not_retried(env):
    run, sent, sleeps, _ = env
    with pytest.raises(up.UploadPostError, match="400"):
        run([urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"bad title"))])
    assert len(sent) == 1 and sleeps == []


def test_dropped_connection_is_treated_like_a_gateway_error(env):
    run, sent, _, _ = env
    assert run([urllib.error.URLError("reset"), b'{"success": true}'])["success"]
    assert len(sent) == 2
