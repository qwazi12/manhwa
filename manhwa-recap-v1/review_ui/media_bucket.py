"""Finished videos in a Railway Storage Bucket (owner approved the bucket
"recap-media", 2026-11-22).

Why: Railway bills SERVICE egress ($0.05/GB) but bucket egress is free. Every
byte this server sends out — a video streamed to the site, a video sent to
Upload-Post — is billed; the same bytes fetched by the browser or Upload-Post
straight from the bucket through a presigned URL are not. Uploading a video
INTO the bucket is service egress too, so each video is uploaded once and then
served/posted from there as often as needed.

Off unless MEDIA_BUCKET=1. Off = the studio behaves exactly as before and none
of the variables below are read.

Credentials come only from the environment, named as Railway's bucket
variable references name them (docs.railway.com/storage-buckets):
  BUCKET              the S3 bucket name (not RAILWAY_BUCKET_NAME)
  ACCESS_KEY_ID
  SECRET_ACCESS_KEY
  ENDPOINT            e.g. https://t3.storageapi.dev
  REGION
With MEDIA_BUCKET=1 and any of them missing, check_config() raises at startup
naming the missing variable(s): no half-configured mode.

Non-secret switches (all optional):
  MEDIA_BUCKET_SKIP_DRIVE=1     stop copying videos to Google Drive; the bucket is
                                the video archive. Honoured only with MEDIA_BUCKET=1.
  MEDIA_BUCKET_POST_BY_URL=0    keep sending video bytes to Upload-Post (default 1:
                                send a presigned link instead).
  MEDIA_BUCKET_URL_TTL          seconds a browser link lives (default 21600 = 6 h)
  MEDIA_BUCKET_POST_TTL         seconds Upload-Post's link lives (default 86400)
  MEDIA_BUCKET_PASS_LIMIT       uploads per background pass (default 4)
  MEDIA_BUCKET_PATH_STYLE=1     path-style URLs (older Railway buckets need it;
                                the bucket's Credentials tab says which)

Every uploaded video is recorded in <project>/bucket.json with its size,
sha256 and the local file's identity. A record is written only after the
object has been read back from the bucket and its sha256 matched — the only
evidence that lets the studio redirect to it or delete its own copy.
"""

import hashlib
import json
import os
import threading
import time

REQUIRED = ("BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY", "ENDPOINT", "REGION")
RECORD = "bucket.json"
CHUNK = 8 * 1024 * 1024

_client = {"value": None}
_lock = threading.Lock()
STATUS = {"last_pass_at": None, "last_pass_uploaded": 0, "uploaded_total": 0,
          "bytes_total": 0, "deleted_total": 0, "last_error": None, "running": False}


class BucketError(Exception):
    pass


def enabled():
    return os.environ.get("MEDIA_BUCKET", "").strip() == "1"


def skip_drive():
    """Drive video copies stop only when the bucket is on and verified copies
    replace them; on its own this switch is ignored (no video without an archive)."""
    return enabled() and os.environ.get("MEDIA_BUCKET_SKIP_DRIVE", "").strip() == "1"


def post_by_url():
    return enabled() and os.environ.get("MEDIA_BUCKET_POST_BY_URL", "1").strip() != "0"


def _int_env(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        raise RuntimeError(f"{name} must be a whole number of seconds/items, got {os.environ.get(name)!r}")


def url_ttl():
    return _int_env("MEDIA_BUCKET_URL_TTL", 21600)


def post_ttl():
    return _int_env("MEDIA_BUCKET_POST_TTL", 86400)


def pass_limit():
    return _int_env("MEDIA_BUCKET_PASS_LIMIT", 4)


def missing():
    return [n for n in REQUIRED if not (os.environ.get(n) or "").strip()]


def check_config():
    """Startup check. Flag off: nothing is required. Flag on: every variable
    must be set, the numbers must parse and boto3 must import — else raise."""
    if not enabled():
        return False
    gone = missing()
    if gone:
        raise RuntimeError("MEDIA_BUCKET=1 but these bucket variables are not set: " + ", ".join(gone)
                           + " (link them from the recap-media bucket in Railway, or set MEDIA_BUCKET=0)")
    url_ttl(), post_ttl(), pass_limit()
    if not 60 <= post_ttl() <= 7 * 86400 or not 60 <= url_ttl() <= 7 * 86400:
        raise RuntimeError("MEDIA_BUCKET_URL_TTL / MEDIA_BUCKET_POST_TTL must be between 60 and 604800 seconds")
    try:
        import boto3  # noqa: F401
    except ImportError:
        raise RuntimeError("MEDIA_BUCKET=1 but boto3 is not installed (deploy/requirements.txt)")
    return True


def client():
    """The S3 client (boto3), built once. Tests replace it with set_client()."""
    with _lock:
        if _client["value"] is None:
            import boto3
            from botocore.config import Config
            style = "path" if os.environ.get("MEDIA_BUCKET_PATH_STYLE", "").strip() == "1" else "virtual"
            _client["value"] = boto3.client(
                "s3",
                endpoint_url=os.environ["ENDPOINT"],
                region_name=os.environ["REGION"],
                aws_access_key_id=os.environ["ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["SECRET_ACCESS_KEY"],
                config=Config(signature_version="s3v4", s3={"addressing_style": style},
                              retries={"max_attempts": 5, "mode": "standard"},
                              connect_timeout=20, read_timeout=120))
        return _client["value"]


def set_client(c):
    with _lock:
        _client["value"] = c


def bucket():
    return os.environ["BUCKET"]


def key_for(pid, name):
    pid, name = os.path.basename(pid.rstrip("/")), os.path.basename(name)
    if not pid or not name or pid.startswith(".") or name.startswith("."):
        raise BucketError(f"bad object name {pid!r}/{name!r}")
    return f"exports/{pid}/{name}"


# ------------------------------------------------------------------ records
def load(pdir):
    try:
        with open(os.path.join(pdir, RECORD), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("objects"), dict) else {"objects": {}}
    except (OSError, ValueError):
        return {"objects": {}}


def _save(pdir, d):
    tmp = os.path.join(pdir, RECORD + f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, os.path.join(pdir, RECORD))


def _identity(path):
    st = os.stat(path)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ino": st.st_ino}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def entry(pdir, name):
    """The verified record for exports/<name>, or None."""
    rec = load(pdir)["objects"].get(os.path.basename(name))
    return rec if isinstance(rec, dict) and rec.get("verified") and rec.get("key") else None


def current(pdir, name):
    """The verified record when it still describes the video: the local file
    is gone (the bucket holds the only working copy) or it is exactly the file
    that was uploaded (same size, mtime and inode). A video re-encoded in place
    under the same name (the phone fix) no longer matches until it is re-sent."""
    rec = entry(pdir, name)
    if not rec:
        return None
    local = os.path.join(pdir, "exports", os.path.basename(name))
    if not os.path.exists(local):
        return rec
    try:
        ident = _identity(local)
    except OSError:
        return None
    if any(ident[k] != rec.get(k) for k in ("size", "mtime_ns", "ino")):
        return None
    return rec


# ------------------------------------------------------------------ transfers
def _remote_sha256(key, c=None):
    c = c or client()
    body = c.get_object(Bucket=bucket(), Key=key)["Body"]
    h = hashlib.sha256()
    try:
        for block in iter(lambda: body.read(CHUNK), b""):
            h.update(block)
    finally:
        try:
            body.close()
        except Exception:
            pass
    return h.hexdigest()


def upload_verified(pdir, name, c=None):
    """Upload exports/<name>, read it back, and record it only when the bucket's
    bytes hash to the local sha256. Returns the record; raises BucketError."""
    c = c or client()
    pid = os.path.basename(pdir.rstrip("/"))
    name = os.path.basename(name)
    local = os.path.join(pdir, "exports", name)
    before = _identity(local)
    digest = sha256_file(local)
    key = key_for(pid, name)
    c.upload_file(local, bucket(), key,
                  ExtraArgs={"ContentType": "video/mp4", "Metadata": {"sha256": digest}})
    head = c.head_object(Bucket=bucket(), Key=key)
    if int(head.get("ContentLength", -1)) != before["size"]:
        raise BucketError(f"{key}: the bucket holds {head.get('ContentLength')} bytes, the file has {before['size']}")
    if _remote_sha256(key, c) != digest:
        raise BucketError(f"{key}: the bucket's copy does not hash to the local sha256")
    if _identity(local) != before:
        raise BucketError(f"{key}: the file changed while it was being uploaded — not recorded")
    rec = {"key": key, "sha256": digest, "at": time.time(), "verified": True, **before}
    with _lock:
        d = load(pdir)
        d["objects"][name] = rec
        _save(pdir, d)
    return rec


def still_there(rec, c=None):
    """Cheap re-check before a local delete: the object exists with the
    recorded size and sha256 metadata."""
    c = c or client()
    try:
        head = c.head_object(Bucket=bucket(), Key=rec["key"])
    except Exception:
        return False
    meta = {k.lower(): v for k, v in (head.get("Metadata") or {}).items()}
    return int(head.get("ContentLength", -1)) == rec.get("size") and meta.get("sha256") == rec.get("sha256")


def presigned_get(rec, ttl, filename=None, attachment=False, c=None):
    c = c or client()
    params = {"Bucket": bucket(), "Key": rec["key"], "ResponseContentType": "video/mp4"}
    if filename:
        safe = os.path.basename(filename).replace('"', "")
        params["ResponseContentDisposition"] = f'{"attachment" if attachment else "inline"}; filename="{safe}"'
    return c.generate_presigned_url("get_object", Params=params, ExpiresIn=int(ttl))


def download_verified(pdir, name, c=None):
    """Bring exports/<name> back from the bucket (free: bucket egress). The
    file takes its real name only after its sha256 matches the record."""
    c = c or client()
    rec = entry(pdir, name)
    if not rec:
        raise BucketError(f"{name} has no verified bucket copy")
    dest = os.path.join(pdir, "exports", os.path.basename(name))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = f"{dest}.{os.getpid()}.part"
    try:
        c.download_file(bucket(), rec["key"], part)
        if os.path.getsize(part) != rec.get("size") or sha256_file(part) != rec.get("sha256"):
            raise BucketError(f"{rec['key']}: the downloaded copy does not match the recorded size/sha256")
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)
    with _lock:                       # the restored file is the recorded bytes: keep redirects valid
        d = load(pdir)
        d["objects"][os.path.basename(name)] = {**rec, **_identity(dest)}
        _save(pdir, d)
    return dest


def newest(pdir):
    """Name of the newest verified video in the bucket for this project."""
    objs = {n: r for n, r in load(pdir)["objects"].items() if isinstance(r, dict) and r.get("verified")}
    return max(objs, key=lambda n: objs[n].get("mtime_ns") or 0) if objs else None
