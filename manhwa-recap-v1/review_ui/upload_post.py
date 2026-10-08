# Upload-Post Client
import json
import os
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
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


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
            detail = e.read().decode("utf-8")[:400]
        except Exception:
            pass
        raise UploadPostError(f"Upload-Post returned {e.code}: {detail}", e.code)
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


def _encode_multipart_formdata(fields, files):
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in fields:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for name, filename, content_type, file_bytes in files:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n".encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(file_bytes)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def upload_video_post(video_path, metadata, targets, thumbnail_path=None, cfg=None, _http=None, on_step=None):
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
            platforms.add(net)
            user_profile = p_user
        else:
            platforms.add(t)

    if not platforms:
        raise UploadPostError("No target platforms selected for publishing")

    fields = [
        ("user", user_profile),
        ("title", metadata.get("title") or "Manhwa Recap"),
        ("async_upload", "true"),
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

    files = []
    if on_step:
        on_step(f"Reading video file ({os.path.basename(video_path)})")
    with open(video_path, "rb") as vf:
        video_bytes = vf.read()
    files.append(("video", os.path.basename(video_path), "video/mp4", video_bytes))

    if thumbnail_path and os.path.exists(thumbnail_path):
        if on_step:
            on_step("Attaching thumbnail")
        with open(thumbnail_path, "rb") as tf:
            thumb_bytes = tf.read()
        mime = "image/png" if thumbnail_path.endswith(".png") else "image/jpeg"
        files.append(("thumbnail", os.path.basename(thumbnail_path), mime, thumb_bytes))

    if on_step:
        on_step("Uploading multipart payload to Upload-Post")

    body_bytes, content_type = _encode_multipart_formdata(fields, files)

    if _http is not None:
        return _http("POST", f"{API_BASE}/upload", body_bytes, cfg)

    req = urllib.request.Request(
        f"{API_BASE}/upload",
        data=body_bytes,
        method="POST",
        headers={
            "Authorization": f"Apikey {cfg['api_key']}",
            "Content-Type": content_type,
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as r:
            raw = r.read().decode("utf-8")
            res = json.loads(raw) if raw else {}
            return res
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:500]
        except Exception:
            pass
        raise UploadPostError(f"Upload failed ({e.code}): {detail}", e.code)
    except urllib.error.URLError as e:
        raise UploadPostError(f"Could not reach Upload-Post: {e.reason}")
