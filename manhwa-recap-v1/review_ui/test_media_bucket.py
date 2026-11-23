"""Media bucket (MEDIA_BUCKET=1, 2026-11-22). Videos are served to the site and
handed to Upload-Post as presigned bucket links (free bucket egress) instead of
being sent by Railway (billed egress). Off by default, and off means exactly
the old behaviour. A bucket copy counts only after its bytes were read back and
hashed to the local sha256; a local video is deleted only after that, and only
when it is posted/merged and not queued. Missing bucket variables with the flag
on stop the boot. Never touches a real bucket: S3 is an in-memory fake."""
import hashlib
import io
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import media_bucket as mb  # noqa: E402
import server as srv  # noqa: E402
import upload_post  # noqa: E402

BUCKET_ENV = {"BUCKET": "recap-media-test", "ACCESS_KEY_ID": "test-id", "SECRET_ACCESS_KEY": "test-secret",
              "ENDPOINT": "https://bucket.invalid", "REGION": "auto"}


class FakeS3:
    """S3 in memory. `corrupt` stores different bytes than were sent."""

    def __init__(self, corrupt=False):
        self.objs, self.meta, self.corrupt, self.uploads = {}, {}, corrupt, []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        with open(path, "rb") as f:
            data = f.read()
        self.uploads.append(key)
        self.objs[key] = data[:-1] + b"!" if self.corrupt else data
        self.meta[key] = dict((ExtraArgs or {}).get("Metadata") or {})

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise KeyError(Key)
        return {"ContentLength": len(self.objs[Key]), "Metadata": self.meta[Key]}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objs[Key])}

    def download_file(self, bucket, key, dest):
        with open(dest, "wb") as f:
            f.write(self.objs[key])

    def generate_presigned_url(self, op, Params, ExpiresIn):
        assert op == "get_object"
        disp = Params.get("ResponseContentDisposition", "")
        return f"https://bucket.invalid/{Params['Key']}?X-Amz-Expires={ExpiresIn}&disp={disp.split(';')[0]}"


@pytest.fixture
def off(monkeypatch):
    for k in ("MEDIA_BUCKET", "MEDIA_BUCKET_SKIP_DRIVE", "MEDIA_BUCKET_POST_BY_URL", *BUCKET_ENV):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("MEDIA_BUCKET", "1")
    for k, v in BUCKET_ENV.items():
        monkeypatch.setenv(k, v)
    s3 = FakeS3()
    mb.set_client(s3)
    yield s3
    mb.set_client(None)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    p = root / "s_5"
    (p / "exports").mkdir(parents=True)
    v = p / "exports" / "v.mp4"
    v.write_bytes(b"video-bytes" * 100)
    old = time.time() - 3600
    os.utime(v, (old, old))
    import ingest
    monkeypatch.setattr(ingest, "PROJECTS", str(root))
    monkeypatch.setattr(srv._pq, "protected", lambda r: set())
    monkeypatch.setattr(srv, "_ev", lambda *a, **k: None)
    return p


def _posted(p):
    (p / srv.PUBLISHES_NAME).write_text(json.dumps({"v.mp4": {"results": [{"status": "published"}]}}))


def _client(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SHARED_SECRET", "s")
    return TestClient(srv.app), {"x-shared-secret": "s"}


# ------------------------------------------------------------ flag off
def test_flag_off_needs_nothing_and_changes_nothing(off, proj, monkeypatch):
    assert mb.check_config() is False and not mb.enabled() and not mb.skip_drive() and not mb.post_by_url()
    # a stray record must not be used while the flag is off
    (proj / "bucket.json").write_text(json.dumps({"objects": {"v.mp4": {"key": "k", "verified": True}}}))
    assert srv._bucket_pass() == 0 and srv._bucket_url(str(proj), "v.mp4", 60) is None
    assert (proj / "exports" / "v.mp4").exists()
    c, h = _client(monkeypatch)
    r = c.get("/export/v.mp4?project=s_5", headers=h, follow_redirects=False)
    assert r.status_code == 200 and r.content == b"video-bytes" * 100


def test_skip_drive_alone_is_ignored_without_the_bucket(off, proj, monkeypatch):
    monkeypatch.setenv("MEDIA_BUCKET_SKIP_DRIVE", "1")
    assert not mb.skip_drive()
    copied = []
    monkeypatch.setattr(srv._drive, "copy_chapter", lambda *a, **k: copied.append(a) or {})
    monkeypatch.setattr(srv, "_project_label", lambda p: {"series": "S", "chapter": 5})
    monkeypatch.setattr(srv._cs, "project_meta", lambda p: {})
    monkeypatch.setattr(srv._cs, "clean_title", lambda n, u=None: n)
    srv._drive_copy(str(proj), "v.mp4")
    assert copied, "Drive copy must still run when the bucket is off"


# ------------------------------------------------------------ fail fast
def test_missing_variable_fails_fast_naming_it(monkeypatch):
    monkeypatch.setenv("MEDIA_BUCKET", "1")
    for k, v in BUCKET_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SECRET_ACCESS_KEY")
    monkeypatch.setenv("ENDPOINT", " ")
    with pytest.raises(RuntimeError) as e:
        mb.check_config()
    assert "SECRET_ACCESS_KEY" in str(e.value) and "ENDPOINT" in str(e.value) and "ACCESS_KEY_ID," not in str(e.value)


def test_server_will_not_boot_with_the_flag_on_and_a_variable_missing(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in BUCKET_ENV}
    env.update(MEDIA_BUCKET="1", BUCKET="b", MIRROR_DISCOVERY="0")
    r = subprocess.run([sys.executable, "-c", "import server"], cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert "ACCESS_KEY_ID" in r.stderr and "SECRET_ACCESS_KEY" in r.stderr and "REGION" in r.stderr


# ------------------------------------------------------------ redirect
def test_export_redirects_to_the_bucket_only_for_a_verified_current_copy(on, proj, monkeypatch):
    c, h = _client(monkeypatch)
    url = "/export/v.mp4?project=s_5"
    r = c.get(url, headers=h, follow_redirects=False)
    assert r.status_code == 200 and r.content.startswith(b"video-bytes")      # not uploaded yet: streams
    srv._bucket_pass()
    r = c.get(url, headers=h, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://bucket.invalid/exports/s_5/v.mp4")
    assert "X-Amz-Expires=21600" in r.headers["location"] and "disp=inline" in r.headers["location"]
    r = c.get(url, headers={**h, "sec-fetch-dest": "document"}, follow_redirects=False)
    assert "disp=attachment" in r.headers["location"]                            # the Download button
    # re-encoded in place under the same name (phone fix): no longer the uploaded bytes
    (proj / "exports" / "v.mp4").write_bytes(b"other" * 7)
    r = c.get(url, headers=h, follow_redirects=False)
    assert r.status_code == 200 and r.content == b"other" * 7
    # local copy gone but the bucket copy is recorded: still served (from the bucket)
    srv._bucket_pass()
    os.remove(proj / "exports" / "v.mp4")
    r = c.get(url, headers=h, follow_redirects=False)
    assert r.status_code == 302


def test_a_link_failure_falls_back_to_streaming(on, proj, monkeypatch):
    srv._bucket_pass()
    monkeypatch.setattr(on, "generate_presigned_url", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    c, h = _client(monkeypatch)
    r = c.get("/export/v.mp4?project=s_5", headers=h, follow_redirects=False)
    assert r.status_code == 200 and "down" in mb.STATUS["last_error"]


# ------------------------------------------------------------ upload + verify before delete
def test_posted_video_is_uploaded_verified_then_removed(on, proj):
    _posted(proj)
    data = (proj / "exports" / "v.mp4").read_bytes()
    assert srv._bucket_pass() == 1
    assert on.objs["exports/s_5/v.mp4"] == data
    rec = mb.entry(str(proj), "v.mp4")
    assert rec["sha256"] == hashlib.sha256(data).hexdigest() and rec["size"] == len(data)
    assert not (proj / "exports" / "v.mp4").exists()


def test_corrupt_bucket_copy_is_never_recorded_and_the_video_stays(on, proj):
    _posted(proj)
    on.corrupt = True
    assert srv._bucket_pass() == 0
    assert mb.entry(str(proj), "v.mp4") is None and (proj / "exports" / "v.mp4").exists()
    assert "does not hash" in mb.STATUS["last_error"]


def test_object_gone_at_delete_time_keeps_the_video(on, proj):
    srv._bucket_pass()                          # uploaded while unposted: kept
    assert (proj / "exports" / "v.mp4").exists()
    _posted(proj)
    on.objs.clear()                             # the bucket lost it since
    srv._bucket_pass(limit=0)                   # no re-upload this pass
    assert (proj / "exports" / "v.mp4").exists()


def test_local_bytes_changed_since_upload_are_not_deleted(on, proj):
    srv._bucket_pass()
    _posted(proj)
    rec = mb.entry(str(proj), "v.mp4")
    rec_bad = {**rec, "sha256": "0" * 64}
    d = mb.load(str(proj))
    d["objects"]["v.mp4"] = rec_bad
    (proj / "bucket.json").write_text(json.dumps(d))
    on.meta["exports/s_5/v.mp4"]["sha256"] = "0" * 64
    srv._bucket_pass(limit=0)
    assert (proj / "exports" / "v.mp4").exists()


@pytest.mark.parametrize("why", ["not posted", "queued", "fresh file"])
def test_unposted_queued_or_fresh_videos_stay(on, proj, monkeypatch, why):
    if why == "queued":
        _posted(proj)
        monkeypatch.setattr(srv._pq, "protected", lambda r: {("s_5", "v.mp4")})
    if why == "fresh file":
        _posted(proj)
        os.utime(proj / "exports" / "v.mp4", None)     # just written: may still be growing
    srv._bucket_pass()
    assert (proj / "exports" / "v.mp4").exists()
    assert (why == "fresh file") == (mb.entry(str(proj), "v.mp4") is None)


def test_pass_limit_bounds_uploads(on, proj):
    old = time.time() - 3600
    for n in ("a.mp4", "b.mp4", "c.mp4"):
        f = proj / "exports" / n
        f.write_bytes(n.encode() * 10)
        os.utime(f, (old, old))
    assert srv._bucket_pass(limit=2) == 2 and srv._bucket_pass(limit=2) == 2
    assert len(on.uploads) == 4


# ------------------------------------------------------------ Drive + restore
def test_skip_drive_with_the_bucket_on_stops_drive_video_copies(on, proj, monkeypatch):
    monkeypatch.setenv("MEDIA_BUCKET_SKIP_DRIVE", "1")
    monkeypatch.setattr(srv._drive, "copy_chapter", lambda *a, **k: pytest.fail("no Drive copy"))
    assert srv._drive_copy(str(proj), "v.mp4") is None
    monkeypatch.setattr(srv, "_bucket_pass_later", lambda *a: None)
    srv._drive_copy_later(str(proj), "v.mp4")


def test_merge_piece_comes_back_from_the_bucket_first(on, proj, monkeypatch):
    srv._bucket_pass()
    data = (proj / "exports" / "v.mp4").read_bytes()
    os.remove(proj / "exports" / "v.mp4")
    monkeypatch.setattr(srv._drive, "download", lambda *a: pytest.fail("Drive is the fallback only"))
    assert srv._export_or_drive(str(proj)) == "v.mp4"
    srv._restore_from_drive(str(proj), "v.mp4")
    assert (proj / "exports" / "v.mp4").read_bytes() == data
    assert mb.current(str(proj), "v.mp4")       # the restored file is the recorded one: links stay valid


def test_restore_refuses_bytes_that_do_not_match(on, proj):
    srv._bucket_pass()
    os.remove(proj / "exports" / "v.mp4")
    on.objs["exports/s_5/v.mp4"] = b"tampered"
    with pytest.raises(mb.BucketError):
        srv._restore_from_drive(str(proj), "v.mp4")
    assert os.listdir(proj / "exports") == []


# ------------------------------------------------------------ Upload-Post by URL
def test_upload_post_sends_the_link_not_the_bytes(monkeypatch):
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "test_key")
    sent = []
    link = "https://bucket.invalid/exports/s_5/v.mp4?X-Amz-Signature=abc"
    upload_post.upload_video_post("/nonexistent/v.mp4", {"title": "T"}, ["youtube"], video_url=link,
                                  _http=lambda m, u, body, cfg: sent.append(bytes(body)) or {"success": True})
    body = sent[0]
    assert link.encode() in body and b'name="video"\r\n\r\nhttps://' in body
    assert b"filename=" not in body
    with pytest.raises(upload_post.UploadPostError):
        upload_post.upload_video_post("/nonexistent/v.mp4", {"title": "T"}, ["youtube"], video_url="http://x",
                                      _http=lambda *a: {})


def test_publish_job_hands_upload_post_the_bucket_link(on, proj, monkeypatch):
    srv._bucket_pass()
    got = {}

    class Mod:
        rejected = staticmethod(upload_post.rejected)
        classify_response = staticmethod(upload_post.classify_response)

        def upload_video_post(self, video, md, targets, thumbnail_path=None, on_step=None, request_id=None,
                              **kw):
            got.update(kw, video=video)
            return {"success": True, "request_id": request_id}

        def upload_detail(self, rid):
            return {"state": "absent", "platforms": {}}

    store = {}
    monkeypatch.setattr(srv, "_get_publish_backend", lambda: ("upload_post", Mod()))
    monkeypatch.setattr(srv, "publish_eligibility", lambda p, n: {
        "ready": True, "blockers": [], "accounts": {"accounts": [{"account_id": "mk:youtube", "network": "youtube"}]}})
    monkeypatch.setattr(srv, "load_publish", lambda p: {"v.mp4": {"targets": ["mk:youtube"], "title": "t"}})
    monkeypatch.setattr(srv, "publish_defaults", lambda p: {})
    monkeypatch.setattr(srv, "load_publishes", lambda p: store)
    monkeypatch.setattr(srv, "save_publishes", lambda p, d: store.update(d))
    monkeypatch.setattr(srv, "_control_gate", lambda *a, **k: None)
    monkeypatch.setattr(srv, "_persist_job", lambda *a, **k: None)
    monkeypatch.setattr(srv, "_series_master_thumb", lambda p, t: t)
    srv.JOBS["bucketjob"] = {}
    srv._run_publish_job("bucketjob", str(proj), "v.mp4")
    assert got["video_url"].startswith("https://bucket.invalid/exports/s_5/v.mp4")
    assert "X-Amz-Expires=86400" in got["video_url"]
    # MEDIA_BUCKET_POST_BY_URL=0: bytes again
    got.clear()
    store.clear()
    monkeypatch.setenv("MEDIA_BUCKET_POST_BY_URL", "0")
    srv.JOBS["bucketjob2"] = {}
    srv._run_publish_job("bucketjob2", str(proj), "v.mp4")
    assert "video_url" not in got and got["video"].endswith("v.mp4")


# ------------------------------------------------------------ real client, no network
def test_real_boto3_client_signs_links_offline(on, monkeypatch):
    pytest.importorskip("boto3")
    mb.set_client(None)
    monkeypatch.setenv("ENDPOINT", "https://t3.storageapi.invalid")
    url = mb.presigned_get({"key": "exports/s_5/v.mp4"}, 3600, filename="v.mp4")
    assert url.startswith("https://recap-media-test.t3.storageapi.invalid/exports/s_5/v.mp4?")
    assert "X-Amz-Signature=" in url and "X-Amz-Expires=3600" in url and "test-secret" not in url
    mb.set_client(None)
    monkeypatch.setenv("MEDIA_BUCKET_PATH_STYLE", "1")
    assert mb.presigned_get({"key": "k"}, 60).startswith("https://t3.storageapi.invalid/recap-media-test/k?")
