"""Phase C — publishing through Outstand (multi-account).

Replaces the direct Google/YouTube OAuth path. Outstand brokers the connection
to each network and exposes one publish API, so the app never holds a YouTube
refresh token and can target several accounts from one call.

STATUS: no OUTSTAND_API_KEY / OUTSTAND_ORG_ID are configured on this
deployment, so NOTHING here has been exercised against the live service. Every
request is built to the documented shape and driven by an injectable HTTP layer
in tests. Do not describe this as working until a real call succeeds.

API shape used (from Outstand's getting-started docs):
  base            https://api.outstand.so/v1
  auth            Authorization: Bearer <api key>
  connect         https://www.outstand.so/app/api/socials/{network}/{orgId}
                    ?redirect_uri=<our callback>
                  callback returns success|error, account_id,
                  network_unique_id, username
  list accounts   GET  /social-accounts      -> id, network, username, nickname
  publish         POST /posts/               -> containers[], accounts[],
                                                scheduledAt?
  post status     GET  /posts/{post_id}      -> per-account status,
                                                platformPostId, error
  media           POST /media/upload -> PUT upload_url -> POST
                  /media/{id}/confirm
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.outstand.so/v1"
CONNECT_BASE = "https://www.outstand.so/app/api/socials"

# Networks Outstand brokers. The app is not hardcoded to YouTube — Phase D may
# ship for one network while the data model already carries the rest.
NETWORKS = ("youtube", "x", "linkedin", "instagram", "facebook", "threads",
            "tiktok", "pinterest", "google_business", "vimeo", "reddit",
            "bluesky")

ACCOUNTS_FILE = "_outstand_accounts.json"
STATE_FILE = "_outstand_state.json"
STATE_TTL = 600
TIMEOUT = 30


class OutstandError(RuntimeError):
    """A request to Outstand failed. Carries the status code when known."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def config():
    """What is configured and precisely what is missing."""
    key = os.environ.get("OUTSTAND_API_KEY")
    org = os.environ.get("OUTSTAND_ORG_ID")
    redirect = os.environ.get("OUTSTAND_REDIRECT_URI") or ""
    missing = []
    if not key:
        missing.append("OUTSTAND_API_KEY")
    if not org:
        missing.append("OUTSTAND_ORG_ID")
    if not redirect:
        missing.append("OUTSTAND_REDIRECT_URI")
    return {"configured": not missing, "api_key": key, "org_id": org,
            "redirect_uri": redirect, "missing": missing,
            "api_base": API_BASE, "networks": list(NETWORKS)}


# ------------------------------------------------------------------ transport
def _request(method, path, cfg=None, body=None, _http=None):
    """One place that talks to Outstand, so tests can replace it wholesale."""
    cfg = cfg or config()
    if not cfg["api_key"]:
        raise OutstandError("OUTSTAND_API_KEY is not set")
    url = API_BASE + path
    if _http is not None:
        return _http(method, url, body, cfg)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "Accept": "application/json"})
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
        raise OutstandError(f"Outstand returned {e.code}: {detail}", e.code)
    except urllib.error.URLError as e:
        raise OutstandError(f"could not reach Outstand: {e.reason}")


# ------------------------------------------------------------- connect flow
def connect_url(network, state=None, cfg=None):
    """Where to send the browser to link an account for `network`."""
    cfg = cfg or config()
    if not cfg["org_id"]:
        raise OutstandError("OUTSTAND_ORG_ID is not set")
    if not cfg["redirect_uri"]:
        raise OutstandError("OUTSTAND_REDIRECT_URI is not set")
    if network not in NETWORKS:
        raise OutstandError(f"unknown network '{network}'")
    redirect = cfg["redirect_uri"]
    if state:
        # carry our CSRF token through Outstand's redirect_uri so the callback
        # can prove it originated from our connect button
        sep = "&" if "?" in redirect else "?"
        redirect = f"{redirect}{sep}state={urllib.parse.quote(state)}"
    q = urllib.parse.urlencode({"redirect_uri": redirect})
    return f"{CONNECT_BASE}/{network}/{cfg['org_id']}?{q}"


# ------------------------------------------------------------------ storage
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


def record_connection(root, account_id, network=None, username=None,
                      network_unique_id=None, nickname=None):
    """Remember an account the operator linked. Keyed by Outstand's account id."""
    if not account_id:
        raise OutstandError("the callback did not return an account_id")
    accts = load_accounts(root)
    prev = accts.get(account_id) or {}
    accts[account_id] = {
        "account_id": account_id,
        "network": network or prev.get("network"),
        "username": username or prev.get("username"),
        "nickname": nickname or prev.get("nickname"),
        "network_unique_id": network_unique_id or prev.get("network_unique_id"),
        "connected_at": prev.get("connected_at") or time.time(),
        "updated_at": time.time(),
        "active": True,
    }
    save_accounts(root, accts)
    return accts[account_id]


def remove_account(root, account_id):
    accts = load_accounts(root)
    if account_id not in accts:
        return False
    accts.pop(account_id)
    save_accounts(root, accts)
    return True


def new_state(root):
    import secrets
    tok = secrets.token_urlsafe(24)
    _write_private(_path(root, STATE_FILE), {"state": tok, "at": time.time()})
    return tok


def check_state(root, got):
    p = _path(root, STATE_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            rec = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    try:
        os.remove(p)
    except OSError:
        pass
    if not got or rec.get("state") != got:
        return False
    return (time.time() - rec.get("at", 0)) <= STATE_TTL


# ------------------------------------------------------------------- API ops
def list_accounts(cfg=None, _http=None):
    """Accounts Outstand currently holds for this organisation."""
    data = _request("GET", "/social-accounts", cfg, _http=_http)
    items = data if isinstance(data, list) else (data or {}).get("data") or []
    out = []
    for it in items:
        out.append({"account_id": it.get("id"), "network": it.get("network"),
                    "username": it.get("username"), "nickname": it.get("nickname")})
    return out


def sync_accounts(root, cfg=None, _http=None):
    """Reconcile local records with Outstand's list.

    Outstand is the source of truth: an account revoked on their side must stop
    looking connected here, or a publish would be attempted against something
    that no longer exists.
    """
    remote = list_accounts(cfg, _http=_http)
    accts = load_accounts(root)
    seen = set()
    for r in remote:
        aid = r["account_id"]
        if not aid:
            continue
        seen.add(aid)
        prev = accts.get(aid) or {}
        accts[aid] = {**prev, **{k: v for k, v in r.items() if v is not None},
                      "active": True,
                      "connected_at": prev.get("connected_at") or time.time(),
                      "updated_at": time.time()}
    for aid, rec in accts.items():
        if aid not in seen:
            rec["active"] = False
            rec["updated_at"] = time.time()
    save_accounts(root, accts)
    return accts


def accounts_status(root, cfg=None):
    """Connection state for the UI. Never returns the API key."""
    cfg = cfg or config()
    accts = load_accounts(root)
    active = [a for a in accts.values() if a.get("active")]
    if not cfg["configured"]:
        return {"state": "not_configured", "configured": False,
                "missing": cfg["missing"], "accounts": [], "n_active": 0,
                "can_publish": False,
                "detail": "Outstand is not configured on this deployment, so no "
                          "account can be connected yet."}
    return {
        "state": "connected" if active else "no_accounts",
        "configured": True, "missing": [],
        "accounts": sorted(accts.values(),
                           key=lambda a: (not a.get("active"),
                                          a.get("network") or "",
                                          a.get("username") or "")),
        "n_active": len(active),
        "can_publish": bool(active),
        "networks": list(NETWORKS),
        "detail": ("Connected." if active else
                   "Outstand is configured, but no account is linked yet."),
    }


def create_post(containers, accounts, scheduled_at=None, cfg=None, _http=None):
    """Publish. `accounts` is a list of Outstand account ids (or names)."""
    if not containers:
        raise OutstandError("a post needs at least one container")
    if not accounts:
        raise OutstandError("a post needs at least one target account")
    body = {"containers": containers, "accounts": list(accounts)}
    if scheduled_at:
        body["scheduledAt"] = scheduled_at
    return _request("POST", "/posts/", cfg, body=body, _http=_http)


def post_status(post_id, cfg=None, _http=None):
    """Per-account outcome for a published post."""
    if not post_id:
        raise OutstandError("no post id")
    data = _request("GET", f"/posts/{post_id}", cfg, _http=_http)
    results = []
    for it in ((data or {}).get("accounts") or (data or {}).get("results") or []):
        results.append({
            "account_id": it.get("accountId") or it.get("account_id") or it.get("id"),
            "status": it.get("status"),
            "platform_post_id": it.get("platformPostId") or it.get("platform_post_id"),
            "error": it.get("error"),
            "url": it.get("url") or it.get("permalink"),
        })
    return {"post_id": (data or {}).get("id") or post_id,
            "status": (data or {}).get("status"), "results": results, "raw": data}


def request_media_upload(filename, content_type, cfg=None, _http=None):
    return _request("POST", "/media/upload", cfg,
                    body={"filename": filename, "content_type": content_type},
                    _http=_http)


def confirm_media(media_id, size, cfg=None, _http=None):
    return _request("POST", f"/media/{media_id}/confirm", cfg,
                    body={"size": int(size)}, _http=_http)
