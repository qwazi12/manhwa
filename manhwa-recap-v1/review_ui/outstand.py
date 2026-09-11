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

# Identifies this app to Outstand. See the note in _request(): an absent or
# default urllib User-Agent is blocked at their Cloudflare edge.
USER_AGENT = "ManhwaRecapStudio/1.0 (+https://manhwa.nodepilot.dev)"

ACCOUNTS_FILE = "_outstand_accounts.json"
STATE_FILE = "_outstand_state.json"
STATE_TTL = 600
TIMEOUT = 30


class OutstandError(RuntimeError):
    """A request to Outstand failed. Carries the status code when known."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def env_any_case(name):
    """Look up an env var ignoring capitalisation.

    Environment variables are case-sensitive on Linux, and this project already
    mixes conventions (Claude_API_KEY sits beside GEMINI_API_KEY). An exact-case
    lookup silently reports "not configured" when the value is right there under
    a different capitalisation, which is indistinguishable from never having set
    it. Accept any casing and remove the trap.
    """
    v = os.environ.get(name)
    if v:
        return v
    want = name.lower()
    for k, val in os.environ.items():
        if k.lower() == want and val:
            return val
    return None


def config():
    """What is configured and precisely what is missing."""
    key = env_any_case("OUTSTAND_API_KEY")
    org = env_any_case("OUTSTAND_ORG_ID")
    redirect = env_any_case("OUTSTAND_REDIRECT_URI") or ""
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
    # A User-Agent is mandatory in practice. Without one urllib sends
    # "Python-urllib/3.x", which Cloudflare in front of Outstand rejects with
    # error 1010 (browser_signature_banned) BEFORE the API key is even looked
    # at — the failure reads as an auth problem but is not one. Identify the
    # client honestly instead.
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT})
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
        if e.code == 403 and "1010" in detail:
            raise OutstandError(
                "Outstand's Cloudflare edge rejected this client (error 1010, "
                "browser signature). The API key was never checked. If this "
                "persists with a proper User-Agent set, ask Outstand to allow "
                "server-side API clients for your organisation.", e.code)
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


# YouTube config, per outstand.so/docs/configurations/youtube. The field that
# matters most: privacyStatus DEFAULTS TO "public". Omitting it publishes a
# scraped-artwork recap publicly on the owner's channel, so this builder always
# sets it explicitly — there is no code path that leaves it unset.
YT_PRIVACY = ("private", "unlisted", "public")
YT_DEFAULT_CATEGORY = "22"


def build_youtube_config(md, force_private=True):
    """Map Phase B publish metadata onto Outstand's `youtube` object.

    Documented fields only: isShort, categoryId, privacyStatus, madeForKids,
    tags, title. There is NO description field — YouTube's description comes
    from the post content — and no documented thumbnail or playlist field, so
    those are not sent.
    """
    privacy = (md or {}).get("privacy") or "private"
    if privacy not in YT_PRIVACY:
        privacy = "private"
    if force_private:
        privacy = "private"
    cfg = {
        "privacyStatus": privacy,          # never omitted: the default is public
        "categoryId": str((md or {}).get("category_id") or YT_DEFAULT_CATEGORY),
        "madeForKids": bool((md or {}).get("made_for_kids")),
    }
    title = ((md or {}).get("title") or "").strip()
    if title:
        cfg["title"] = title
    tags = [t for t in ((md or {}).get("tags") or []) if t]
    if tags:
        cfg["tags"] = tags
    if (md or {}).get("is_short"):
        cfg["isShort"] = True
    return cfg


def create_post(containers, accounts, scheduled_at=None, youtube=None,
                cfg=None, _http=None):
    """Publish. `accounts` is a list of Outstand account ids, names or handles.

    `youtube` is the top-level per-network config object.
    """
    if not containers:
        raise OutstandError("a post needs at least one container")
    if not accounts:
        raise OutstandError("a post needs at least one target account")
    body = {"containers": containers, "accounts": list(accounts)}
    if scheduled_at:
        body["scheduledAt"] = scheduled_at
    if youtube:
        if "privacyStatus" not in youtube:
            # belt and braces: the API default is public
            raise OutstandError("youtube config must set privacyStatus explicitly")
        body["youtube"] = youtube
    data = _request("POST", "/posts/", cfg, body=body, _http=_http)
    return _parse_post(data)


def _parse_post(data):
    """Normalise Outstand's post envelope.

    Documented shape: {"success": true, "post": {"id", "publishedAt",
    "socialAccounts": [{"id", "network", "username", "status", "error",
    "platformPostId", "publishedAt"}]}}. Note there is NO public URL field —
    one is derived for YouTube from platformPostId rather than invented.
    """
    post = (data or {}).get("post") or data or {}
    results = []
    for it in (post.get("socialAccounts") or []):
        ppid = it.get("platformPostId")
        network = it.get("network")
        url = None
        if ppid and network == "youtube":
            url = f"https://www.youtube.com/watch?v={ppid}"
        results.append({
            "account_id": it.get("id"),
            "network": network,
            "username": it.get("username"),
            "status": it.get("status"),
            "platform_post_id": ppid,
            "published_at": it.get("publishedAt"),
            "error": it.get("error"),
            "url": url,
        })
    return {"post_id": post.get("id"), "published_at": post.get("publishedAt"),
            "scheduled_at": post.get("scheduledAt"), "results": results,
            "raw": data}


def post_status(post_id, cfg=None, _http=None):
    """Per-account outcome for a post. Statuses: pending | published | failed."""
    if not post_id:
        raise OutstandError("no post id")
    return _parse_post(_request("GET", f"/posts/{post_id}", cfg, _http=_http))


def request_media_upload(filename, content_type, cfg=None, _http=None):
    return _request("POST", "/media/upload", cfg,
                    body={"filename": filename, "content_type": content_type},
                    _http=_http)


def confirm_media(media_id, size, cfg=None, _http=None):
    return _request("POST", f"/media/{media_id}/confirm", cfg,
                    body={"size": int(size)}, _http=_http)
