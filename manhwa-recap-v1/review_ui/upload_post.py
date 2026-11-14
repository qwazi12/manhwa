# Upload-Post Client
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API_BASE = "https://api.upload-post.com/api"
USER_AGENT = "ManhwaRecapStudio/1.0 (+https://manhwa.nodepilot.dev)"
ACCOUNTS_FILE = "_upload_post_accounts.json"
TIMEOUT = 30
UPLOAD_TIMEOUT = 1200
UPLOAD_ATTEMPTS = 3                 # gateway errors only; see upload_video_post
GATEWAY_CODES = (502, 503, 504)
RETRY_WAIT = (30, 90)               # seconds before attempts 2 and 3 (+ jitter)

NETWORKS = (
    "youtube",
    "tiktok",
    "instagram",
    "twitter",
    "facebook",
    "threads",
    "linkedin",
    "pinterest",
    "bluesky",
    "discord",
    "telegram",
)


class UploadPostError(RuntimeError):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body          # raw response text, when the server answered


def env_any_case(name):
    v = os.environ.get(name)
    if v:
        return v
    want = name.lower()
    for k, val in os.environ.items():
        if k.lower() == want and val:
            return val
    return None


def config():
    key = (
        env_any_case("UPLOADPOST_API_KEY")
        or env_any_case("UPLOAD_POST_API_KEY")
        or env_any_case("UPLOADPOST_KEY")
    )
    user_profile = (
        env_any_case("UPLOAD_POST_PROFILE")
        or env_any_case("UPLOADPOST_PROFILE")
        or env_any_case("UPLOAD_POST_USER")
        or env_any_case("UPLOADPOST_USER")
        or "mk"
    )
    missing = []
    if not key:
        missing.append("UPLOADPOST_API_KEY")
    return {
        "configured": not missing,
        "api_key": key,
        "user_profile": user_profile,
        "missing": missing,
        "api_base": API_BASE,
        "networks": list(NETWORKS),
    }


def _request(method, path, cfg=None, body=None, headers=None, _http=None):
    cfg = cfg or config()
    if not cfg["api_key"]:
        raise UploadPostError("UPLOADPOST_API_KEY is not set")
    url = API_BASE + path if path.startswith("/") else f"{API_BASE}/{path}"
    if _http is not None:
        return _http(method, url, body, cfg)

    req_headers = {
        "Authorization": f"Apikey {cfg['api_key']}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if headers:
        req_headers.update(headers)

    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        elif isinstance(body, (bytes, bytearray)):
            data = body

    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            pass
        raise UploadPostError(f"Upload-Post returned {e.code}: {detail[:400]}", e.code, detail)
    except urllib.error.URLError as e:
        raise UploadPostError(f"could not reach Upload-Post: {e.reason}")


def _path(root, name):
    return os.path.join(root, name)


def _write_private(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def load_accounts(root):
    try:
        with open(_path(root, ACCOUNTS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_accounts(root, data):
    _write_private(_path(root, ACCOUNTS_FILE), data)


def get_user_profiles(cfg=None, _http=None):
    data = _request("GET", "/uploadposts/users", cfg=cfg, _http=_http)
    profiles = data.get("profiles") or []
    return profiles


def sync_accounts(root, cfg=None, _http=None):
    cfg = cfg or config()
    remote_profiles = get_user_profiles(cfg=cfg, _http=_http)

    accts = load_accounts(root)
    for p in remote_profiles:
        p_user = p.get("username") or ""
        socials = p.get("social_accounts") or {}
        for net, details in socials.items():
            if not details:
                continue
            acct_id = f"{p_user}:{net}"
            display_name = ""
            if isinstance(details, dict):
                display_name = details.get("display_name") or details.get("username") or p_user
            elif isinstance(details, str):
                display_name = details

            accts[acct_id] = {
                "account_id": acct_id,
                "profile_user": p_user,
                "network": net,
                "username": display_name,
                "active": True,
                "updated_at": time.time(),
            }

    save_accounts(root, accts)
    return accts


def upload_status(request_id, cfg=None, _http=None):
    """What happened to an async upload: {"status": "completed"|..., "results":
    [{"profile_username", "platform", "success", "post_url", "error_message"}]}"""
    return _request("GET", "/uploadposts/status?request_id="
                    + urllib.parse.quote(str(request_id)), cfg=cfg, _http=_http)


def get_connect_jwt_url(username, cfg=None, _http=None):
    cfg = cfg or config()
    body = {
        "username": username or cfg["user_profile"],
        "connect_title": "Manhwa Recap Studio Publishing",
    }
    data = _request("POST", "/uploadposts/users/generate-jwt", cfg=cfg, body=body, _http=_http)
    return data.get("access_url")


def accounts_status(root, cfg=None):
    cfg = cfg or config()
    accts = load_accounts(root)
    active = [a for a in accts.values() if a.get("active")]
    if not cfg["configured"]:
        return {
            "state": "not_configured",
            "configured": False,
            "missing": cfg["missing"],
            "accounts": [],
            "n_active": 0,
            "can_publish": False,
            "detail": "Upload-Post is not configured. Missing UPLOADPOST_API_KEY.",
        }
    return {
        "state": "connected" if active else "no_accounts",
        "configured": True,
        "missing": [],
        "accounts": sorted(
            accts.values(),
            key=lambda a: (not a.get("active"), a.get("network") or "", a.get("username") or ""),
        ),
        "n_active": len(active),
        "can_publish": bool(active),
        "networks": list(NETWORKS),
        "detail": (
            f"Connected to Upload-Post (Profile: {cfg['user_profile']})."
            if active
            else f"Upload-Post configured (Profile: {cfg['user_profile']}), but no accounts linked yet."
        ),
    }


def remove_account(root, account_id):
    accts = load_accounts(root)
    if account_id not in accts:
        return False
    accts.pop(account_id)
    save_accounts(root, accts)
    return True


class _Multipart:
    """A multipart/form-data body read from disk as it is sent (2026-11-13).
    Before, the whole video was read into memory and then copied into the
    request body: a 1 GB export held about 2 GB while it uploaded.

    files: (field, filename, content_type, source) where source is bytes or a
    path. `length` is exact, so the request carries Content-Length."""

    CHUNK = 1024 * 1024

    def __init__(self, fields, files):
        self.boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
        self.parts = []                      # bytes, or (path, size)
        b = self.boundary
        for name, value in fields:
            self.parts.append((f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                               .encode("utf-8") + str(value).encode("utf-8") + b"\r\n"))
        for name, filename, content_type, source in files:
            self.parts.append((f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                               f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n").encode("utf-8"))
            if isinstance(source, (bytes, bytearray)):
                self.parts.append(bytes(source))
            else:
                self.parts.append((source, os.path.getsize(source)))
            self.parts.append(b"\r\n")
        self.parts.append(f"--{b}--\r\n".encode("utf-8"))
        self.content_type = f"multipart/form-data; boundary={b}"
        self.length = sum(len(x) if isinstance(x, bytes) else x[1] for x in self.parts)

    def chunks(self):
        for part in self.parts:
            if isinstance(part, bytes):
                yield part
                continue
            path, size = part
            left = size
            with open(path, "rb") as f:
                while left > 0:
                    buf = f.read(min(self.CHUNK, left))
                    if not buf:
                        raise UploadPostError(f"{os.path.basename(path)} changed while it was being uploaded")
                    left -= len(buf)
                    yield buf

    def reader(self):
        """A fresh file-like body (one per attempt): read(n) hands out the
        next bytes, never more than CHUNK + n in memory."""
        return _PartReader(self.chunks())


class _PartReader:
    def __init__(self, chunks):
        self._chunks, self._buf = chunks, b""

    def read(self, n=-1):
        if n is None or n < 0:
            out = self._buf + b"".join(self._chunks)
            self._buf = b""
            return out
        while len(self._buf) < n:
            nxt = next(self._chunks, None)
            if nxt is None:
                break
            self._buf += nxt
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


def _encode_multipart_formdata(fields, files):
    """The whole body in memory — small payloads and tests only."""
    mp = _Multipart(fields, files)
    return bytearray(mp.reader().read()), mp.content_type


def upload_video_post(video_path, metadata, targets, thumbnail_path=None, cfg=None, _http=None, on_step=None,
                      request_id=None):
    """request_id: the caller's own id for this upload, stored BEFORE the bytes
    are sent, so a publish cut off by a restart can ask Upload-Post whether it
    arrived instead of sending the video a second time (2026-11-13). When
    omitted, one is made here as before."""
    cfg = cfg or config()
    if not cfg["api_key"]:
        raise UploadPostError("UPLOADPOST_API_KEY is not set")
    if not os.path.exists(video_path):
        raise UploadPostError(f"Video file not found: {video_path}")

    platforms = set()
    user_profile = cfg["user_profile"]
    for t in targets:
        if ":" in t:
            p_user, net = t.split(":", 1)
            user_profile = p_user
            if net == "*":
                # the accounts store lives in the projects root (server._os_root);
                # HERE was undefined, so any "<profile>:*" target crashed posting
                import ingest as _ing
                accts = load_accounts(_ing.PROJECTS)
                matching = [a["network"] for a in accts.values() if a.get("profile_user") == p_user and a.get("active")]
                if matching:
                    platforms.update(matching)
                else:
                    platforms.update(["youtube", "tiktok", "instagram", "facebook"])
            else:
                platforms.add(net)
        else:
            platforms.add(t)

    if not platforms:
        raise UploadPostError("No target platforms selected for publishing")

    # Our own request id, also the Idempotency-Key: after a gateway error we
    # can ask Upload-Post whether it got the upload, and a resend with the same
    # key can never create a second post (Upload-Post docs, 2026-10-31).
    request_id = request_id or uuid.uuid4().hex
    fields = [
        ("user", user_profile),
        ("title", metadata.get("title") or "Manhwa Recap"),
        ("async_upload", "true"),
        ("request_id", request_id),
    ]
    for p in platforms:
        fields.append(("platform[]", p))

    desc = metadata.get("description") or metadata.get("title") or ""
    if desc:
        fields.append(("description", desc))

    if "youtube" in platforms:
        fields.append(("youtube_title", metadata.get("title") or "Manhwa Recap"))
        fields.append(("youtube_description", desc))
        privacy = metadata.get("privacy") or "private"
        if metadata.get("youtube_publish_at"):
            # YouTube's own scheduled publish (Upload-Post docs: RFC3339, forces
            # private until then): the video goes live at exactly that time
            privacy = "private"
            fields.append(("youtube_publish_at", str(metadata["youtube_publish_at"])))
        fields.append(("privacyStatus", privacy))
        fields.append(("categoryId", str(metadata.get("category_id") or "22")))
        fields.append(("selfDeclaredMadeForKids", "true" if metadata.get("made_for_kids") else "false"))
        fields.append(("containsSyntheticMedia", "true"))
        for tag in (metadata.get("tags") or []):
            if tag:
                fields.append(("tags[]", tag))
        # Spec 10 P2/P3 (2026-10-08): the series playlist (added after it
        # publishes) and the navigation comment. Upload-Post documents both
        # (docs.upload-post.com/api/upload-video); YouTube's API can't pin it.
        if metadata.get("youtube_playlist_id"):
            fields.append(("youtube_playlist_id", str(metadata["youtube_playlist_id"])))
        if metadata.get("first_comment"):
            fields.append(("youtube_first_comment", str(metadata["first_comment"])[:1000]))

    if "tiktok" in platforms:
        fields.append(("tiktok_title", metadata.get("title") or "Manhwa Recap"))
        privacy = metadata.get("privacy") or "private"
        tt_privacy = "SELF_ONLY" if privacy == "private" else "PUBLIC_TO_EVERYONE"
        fields.append(("privacy_level", tt_privacy))
        fields.append(("is_aigc", "true"))
        if metadata.get("first_comment"):
            fields.append(("tiktok_first_comment", metadata["first_comment"]))

    if "instagram" in platforms:
        fields.append(("instagram_title", metadata.get("title") or "Manhwa Recap"))
        fields.append(("media_type", "REELS"))
        fields.append(("is_ai_generated", "true"))

    if "facebook" in platforms:
        fields.append(("facebook_title", metadata.get("title") or "Manhwa Recap"))
        import chapter_seo as _cseo      # owner, 2026-11-07: Facebook keeps 3 hashtags
        fields.append(("facebook_description", _cseo.cap_hashtags(desc, _cseo.PLATFORM_HASHTAG_MAX["facebook"])))

    if "twitter" in platforms or "x" in platforms:
        fields.append(("twitter_text", (metadata.get("title") or "")[:280]))

    if "threads" in platforms:
        fields.append(("threads_text", (metadata.get("title") or "")[:500]))

    if "bluesky" in platforms:
        fields.append(("bluesky_text", (metadata.get("title") or "")[:300]))

    files = [("video", os.path.basename(video_path), "video/mp4", video_path)]   # streamed from disk
    if thumbnail_path and os.path.exists(thumbnail_path):
        if on_step:
            on_step("Attaching thumbnail")
        mime = "image/png" if thumbnail_path.endswith(".png") else "image/jpeg"
        files.append(("thumbnail", os.path.basename(thumbnail_path), mime, thumbnail_path))

    if on_step:
        on_step("Uploading multipart payload to Upload-Post")

    body = _Multipart(fields, files)

    if _http is not None:
        return _http("POST", f"{API_BASE}/upload", bytearray(body.reader().read()), cfg)

    def _req():
        # a new request per attempt: the body is a stream and is used up once sent
        return urllib.request.Request(
            f"{API_BASE}/upload",
            data=body.reader(),
            method="POST",
            headers={
                "Authorization": f"Apikey {cfg['api_key']}",
                "Content-Type": body.content_type,
                "Content-Length": str(body.length),
                "User-Agent": USER_AGENT,
                "Idempotency-Key": request_id,
            },
        )

    # Owner, 2026-10-31: three merged videos never posted after Upload-Post
    # gateway errors (502/504). A gateway error may still have been accepted,
    # so check status first; resend only when Upload-Post has no record.
    for attempt in range(UPLOAD_ATTEMPTS):
        try:
            with urllib.request.urlopen(_req(), timeout=UPLOAD_TIMEOUT) as r:
                raw = r.read().decode("utf-8")
                res = json.loads(raw) if raw else {}
                return res
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                pass
            err = UploadPostError(f"Upload failed ({e.code}): {detail[:500]}", e.code, detail)
            if e.code not in GATEWAY_CODES:
                raise err
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = UploadPostError(f"Could not reach Upload-Post: {getattr(e, 'reason', e)}")
        # Ask before sending again. Only a documented "no such upload" answer
        # permits a resend (same id, same Idempotency-Key); if Upload-Post
        # cannot say, stop here and let the caller reconcile later.
        state = upload_detail(request_id, cfg=cfg)["state"]
        if state in ("accepted", "failed"):
            if on_step:
                on_step("Upload-Post had received the upload despite the gateway error")
            return {"success": True, "request_id": request_id, "recovered_after": str(err)}
        if state == "unknown" or attempt + 1 == UPLOAD_ATTEMPTS:
            raise err
        if on_step:
            on_step(f"{err} — Upload-Post has no record of it; retrying "
                    f"(attempt {attempt + 2} of {UPLOAD_ATTEMPTS})")
        time.sleep(RETRY_WAIT[attempt] + random.uniform(0, 10))


# ---------------------------------------------------------------- outcomes
# From Upload-Post's documentation (docs.upload-post.com/api/upload-video and
# /api/upload-status, read 2026-11-14):
#   * request_id may be supplied by the client and is returned in every answer;
#   * an Idempotency-Key makes a repeated request return the existing job
#     instead of creating a second one;
#   * error answers are JSON with "success": false — 400 (bad form), 401, 403,
#     404 (user not found), 429 (monthly limit); a 500 is a server error;
#   * status of an unknown id: HTTP 404 with {"status": "not_found"};
#   * status values: pending, queued, processing, in_progress, completed,
#     failed ("all platforms have failed, or no activity for over 1 hour"),
#     not_found; per platform: queued, processing, completed, failed,
#     retryable, skipped, each with a "success" flag.
# Anything outside what is documented is treated as "unknown", never as a
# refusal and never as "nothing was received".
_REFUSAL_CODES = (400, 401, 403, 404, 429)
_LIVE_STATES = ("pending", "queued", "processing", "in_progress", "completed")
_PLATFORM_WAIT = ("queued", "processing", "retryable", "pending", "in_progress")


def _json_object(text):
    try:
        v = json.loads(text) if text else None
    except (ValueError, TypeError):
        return None
    return v if isinstance(v, dict) else None


def rejected(exc):
    """True only when this error PROVES Upload-Post refused the upload: one of
    its documented error answers — a refusal status code whose body is JSON
    saying "success": false. A timeout, a dropped connection, a 5xx, an empty
    or non-JSON body proves nothing."""
    if not isinstance(exc, UploadPostError) or exc.status not in _REFUSAL_CODES:
        return False
    body = _json_object(getattr(exc, "body", None))
    return bool(body) and body.get("success") is False


def classify_response(resp):
    """What an answer to POST /upload establishes:
      ("rejected", message)           it says "success": false
      ("finished", {platform: {...}}) synchronous: per-platform results
      ("accepted", ids)               "success": true — background / scheduled;
                                      ids = {"request_id", "job_id"} as returned
      ("unknown", why)                empty, not an object, or no boolean
                                      "success": nothing can be concluded"""
    if not isinstance(resp, dict) or not isinstance(resp.get("success"), bool):
        return "unknown", "Upload-Post's answer was empty or not in its documented form"
    if resp["success"] is False:
        return "rejected", str(resp.get("message") or resp.get("error") or "Upload-Post refused the upload")[:300]
    if isinstance(resp.get("results"), dict) and resp["results"]:
        return "finished", {str(k): _platform_outcome(v if isinstance(v, dict) else {}) for k, v in resp["results"].items()}
    return "accepted", {"request_id": resp.get("request_id") or None, "job_id": resp.get("job_id") or None}


def _platform_outcome(x):
    """One platform's entry (status answer or synchronous result) ->
    {"state": "published" | "failed" | "pending", ...}."""
    st = str(x.get("status") or "").lower()
    if x.get("success") is True:
        return {"state": "published", "url": x.get("post_url") or x.get("url"),
                "platform_post_id": x.get("platform_post_id"), "raw": x}
    if st in _PLATFORM_WAIT:
        return {"state": "pending", "raw": x}
    if x.get("success") is False or st in ("failed", "skipped") or x.get("skipped") is True or x.get("error"):
        return {"state": "failed", "error": str(x.get("error_message") or x.get("message") or x.get("error")
                                                 or x.get("skip_reason") or "the platform rejected the upload")[:300],
                "raw": x}
    return {"state": "pending", "raw": x}


def upload_detail(request_id, cfg=None, _http=None):
    """What Upload-Post knows about this request id:
      state "accepted"  it has the upload (waiting, running or done)
            "failed"    it has it and reports the whole job failed
            "absent"    its documented "no such upload" answer
            "unknown"   it could not be asked, or answered something undocumented
      platforms {name: {"state": "published" | "failed" | "pending", ...}}"""
    try:
        st = upload_status(request_id, cfg=cfg or config(), _http=_http)
    except UploadPostError as e:
        body = _json_object(getattr(e, "body", None))
        if e.status == 404 and body and body.get("status") == "not_found":
            return {"state": "absent", "platforms": {}}
        return {"state": "unknown", "platforms": {}, "why": str(e)[:200]}
    except Exception as e:  # noqa — network, bad JSON, anything
        return {"state": "unknown", "platforms": {}, "why": f"{type(e).__name__}: {e}"[:200]}
    if not isinstance(st, dict):
        return {"state": "unknown", "platforms": {}, "why": "the status answer was not an object"}
    status = st.get("status")
    if status == "not_found":
        return {"state": "absent", "platforms": {}}
    if status not in _LIVE_STATES and status != "failed":
        return {"state": "unknown", "platforms": {}, "why": f"undocumented status {status!r}"}
    platforms = {}
    for x in st.get("results") or []:
        if isinstance(x, dict) and x.get("platform"):
            platforms[str(x["platform"])] = {**_platform_outcome(x), "profile": x.get("profile_username")}
    return {"state": "failed" if status == "failed" else "accepted", "status": status, "platforms": platforms}


def upload_state(request_id, cfg=None, _http=None):
    return upload_detail(request_id, cfg=cfg, _http=_http)["state"]


def accepted(request_id, cfg=None):
    """Public form of _accepted: does Upload-Post already hold an upload with
    this request id?"""
    return _accepted(request_id, cfg or config())


def _accepted(request_id, cfg):
    """Does Upload-Post hold this upload? Unknown -> False; callers that must
    not send twice use upload_detail and treat "unknown" separately."""
    return upload_detail(request_id, cfg=cfg)["state"] in ("accepted", "failed")
