"""Phase C — YouTube account connection.

SCAFFOLDING. No Google OAuth credentials are configured for this deployment, so
the connection flow below has NEVER been exercised against Google's live
endpoints. Everything that does not need a secret is implemented and tested:
config detection, the authorize URL, the token-exchange and refresh request
shapes (with an injectable HTTP layer so tests drive them), account storage,
status reporting, and disconnect. The live handshake is unverified — say so
rather than implying it works.

Storage note: a refresh token is a long-lived credential. It is written to the
Railway volume as plaintext JSON with 0600 permissions. That is acceptable for
a single-operator internal tool on a private volume and NOT acceptable for a
multi-tenant one; encrypting at rest or moving to a secret manager is the
upgrade path.
"""
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
CHANNELS_ENDPOINT = ("https://www.googleapis.com/youtube/v3/channels"
                     "?part=snippet&mine=true")

# youtube.upload is the minimum to publish. youtube.readonly is requested only
# so the UI can name the connected channel — without it the operator cannot
# tell WHICH account they linked, which is how videos end up on the wrong one.
SCOPES = ("https://www.googleapis.com/auth/youtube.upload "
          "https://www.googleapis.com/auth/youtube.readonly")

ACCOUNT_FILE = "_youtube_account.json"
STATE_FILE = "_youtube_oauth_state.json"
STATE_TTL = 600          # an unfinished connect attempt expires in 10 minutes


def oauth_config():
    """What is configured, and precisely what is missing.

    Accepts either GOOGLE_* or YOUTUBE_* names so whichever the operator
    creates in Google Cloud works without a second decision.
    """
    cid = os.environ.get("GOOGLE_CLIENT_ID") or os.environ.get("YOUTUBE_CLIENT_ID")
    sec = (os.environ.get("GOOGLE_CLIENT_SECRET")
           or os.environ.get("YOUTUBE_CLIENT_SECRET"))
    redirect = (os.environ.get("OAUTH_REDIRECT_URI")
                or os.environ.get("YOUTUBE_REDIRECT_URI") or "")
    missing = []
    if not cid:
        missing.append("GOOGLE_CLIENT_ID")
    if not sec:
        missing.append("GOOGLE_CLIENT_SECRET")
    if not redirect:
        missing.append("OAUTH_REDIRECT_URI")
    return {"configured": not missing, "client_id": cid, "client_secret": sec,
            "redirect_uri": redirect, "missing": missing, "scopes": SCOPES}


# ------------------------------------------------------------------ storage
def _path(root, name):
    return os.path.join(root, name)


def _write_private(path, data):
    """Write JSON with owner-only permissions — it holds a refresh token."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def load_account(root):
    try:
        with open(_path(root, ACCOUNT_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_account(root, data):
    _write_private(_path(root, ACCOUNT_FILE), data)


def clear_account(root):
    try:
        os.remove(_path(root, ACCOUNT_FILE))
        return True
    except OSError:
        return False


def new_state(root):
    """One-time CSRF token for the redirect round-trip."""
    tok = secrets.token_urlsafe(24)
    _write_private(_path(root, STATE_FILE), {"state": tok, "at": time.time()})
    return tok


def check_state(root, got):
    """True only for the token we issued, unexpired, and only once."""
    p = _path(root, STATE_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            rec = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    try:
        os.remove(p)                      # single use, whatever the outcome
    except OSError:
        pass
    if not got or rec.get("state") != got:
        return False
    return (time.time() - rec.get("at", 0)) <= STATE_TTL


# -------------------------------------------------------------- oauth flow
def authorize_url(state, cfg=None):
    """Google's consent URL.

    access_type=offline + prompt=consent because a refresh token is only
    returned on the first consent unless consent is re-requested; without it a
    reconnect silently yields an access token that expires in an hour.
    """
    cfg = cfg or oauth_config()
    if not cfg["configured"]:
        raise RuntimeError("OAuth is not configured: missing "
                           + ", ".join(cfg["missing"]))
    q = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": cfg["scopes"],
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return AUTH_ENDPOINT + "?" + urllib.parse.urlencode(q)


def _post_form(url, fields, _http=None):
    """POST a form and return parsed JSON. `_http` lets tests drive it."""
    if _http is not None:
        return _http(url, fields)
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url, token, _http=None):
    if _http is not None:
        return _http(url, token)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def exchange_code(code, cfg=None, _http=None):
    """Swap the callback code for tokens. UNVERIFIED against live Google."""
    cfg = cfg or oauth_config()
    if not cfg["configured"]:
        raise RuntimeError("OAuth is not configured")
    return _post_form(TOKEN_ENDPOINT, {
        "code": code, "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": cfg["redirect_uri"],
        "grant_type": "authorization_code"}, _http=_http)


def refresh_access_token(refresh_token, cfg=None, _http=None):
    cfg = cfg or oauth_config()
    if not cfg["configured"]:
        raise RuntimeError("OAuth is not configured")
    return _post_form(TOKEN_ENDPOINT, {
        "refresh_token": refresh_token, "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "grant_type": "refresh_token"}, _http=_http)


def fetch_channel(access_token, _http=None):
    """The connected channel's id and title, so the UI can name it."""
    data = _get_json(CHANNELS_ENDPOINT, access_token, _http=_http)
    items = (data or {}).get("items") or []
    if not items:
        return {}
    it = items[0]
    return {"channel_id": it.get("id"),
            "channel_title": ((it.get("snippet") or {}).get("title"))}


def store_tokens(root, tokens, channel=None):
    """Persist a connection. A refresh token is only issued on first consent,
    so an existing one is KEPT when a later response omits it."""
    prev = load_account(root)
    rec = {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token") or prev.get("refresh_token"),
        "expires_at": time.time() + float(tokens.get("expires_in") or 0),
        "scope": tokens.get("scope") or SCOPES,
        "token_type": tokens.get("token_type") or "Bearer",
        "connected_at": prev.get("connected_at") or time.time(),
        "updated_at": time.time(),
        "channel_id": (channel or {}).get("channel_id") or prev.get("channel_id"),
        "channel_title": (channel or {}).get("channel_title") or prev.get("channel_title"),
    }
    save_account(root, rec)
    return rec


def account_status(root, cfg=None):
    """Connection state for the UI. NEVER returns token material."""
    cfg = cfg or oauth_config()
    acc = load_account(root)
    if not cfg["configured"]:
        return {"state": "not_configured", "connected": False,
                "can_upload": False, "missing": cfg["missing"],
                "detail": "Google OAuth credentials are not set on this "
                          "deployment, so no account can be connected yet."}
    if not acc:
        return {"state": "disconnected", "connected": False, "can_upload": False,
                "missing": [], "detail": "No YouTube account is connected."}
    has_refresh = bool(acc.get("refresh_token"))
    expired = float(acc.get("expires_at") or 0) <= time.time()
    return {
        "state": "connected" if has_refresh else "needs_reauth",
        "connected": True,
        # Without a refresh token the connection dies in an hour and an upload
        # would fail halfway, so it does not count as upload-capable.
        "can_upload": has_refresh,
        "missing": [] if has_refresh else ["refresh_token"],
        "channel_id": acc.get("channel_id"),
        "channel_title": acc.get("channel_title"),
        "connected_at": acc.get("connected_at"),
        "access_expired": expired,
        "scope": acc.get("scope"),
        "detail": ("Connected." if has_refresh else
                   "Connected, but no refresh token was issued — reconnect so "
                   "the link survives beyond an hour."),
    }
