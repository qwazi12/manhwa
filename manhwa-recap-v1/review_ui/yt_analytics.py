"""Read-only YouTube Analytics for the growth lane (F3, owner 2026-11-06).

Shared with the Scrapper app (its editorial/audience.py): the same env var,
the same Google "authorized_user" grant format, the single scope
yt-analytics.readonly, and Pacific reporting days. One sign-in method
(manhwa-recap-v1/tools/yt_analytics_signin.py) makes a grant for either app.

Nothing here writes to disk. The access token lives in memory only, and no
error message ever contains the grant, the refresh token or the access token.
"""
import datetime as _dt
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ENV = "YOUTUBE_ANALYTICS_AUTHORIZED_USER_JSON"
SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REPORTS = "https://youtubeanalytics.googleapis.com/v2/reports"
# Scrapper's names first (same meaning in both apps), then what FL-1/FL-4 need.
METRICS = ("views", "engagedViews", "likes", "comments", "averageViewDuration",
           "averageViewPercentage", "estimatedMinutesWatched", "subscribersGained",
           "subscribersLost")
MAX_DAYS = 400

_tok = {"value": None, "exp": 0.0}
_lock = threading.Lock()


class AnalyticsError(RuntimeError):
    pass


class NotConfigured(AnalyticsError):
    pass


class InputError(ValueError):
    pass


def _grant():
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        raise NotConfigured(f"{ENV} is not set; the owner's read-only sign-in has not been done")
    try:
        info = json.loads(raw)
    except ValueError:
        raise AnalyticsError(f"{ENV} is not valid JSON") from None
    scopes = info.get("scopes") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not info.get(k)]
    if info.get("type") != "authorized_user" or missing:
        raise AnalyticsError(f"{ENV} must be an authorized_user grant (missing: {', '.join(missing) or 'type'})")
    if SCOPE not in scopes:
        raise AnalyticsError(f"{ENV} must include the yt-analytics.readonly scope")
    if info.get("token_uri", TOKEN_URI) != TOKEN_URI:
        raise AnalyticsError(f"{ENV} has an unsupported token endpoint")
    return info


def status():
    """Configured or not, and why. No network call, never echoes the grant."""
    try:
        _grant()
        return {"configured": True, "scope": SCOPE, "env": ENV,
                "note": "grant set; the first report confirms which channel it can read"}
    except AnalyticsError as e:
        return {"configured": False, "scope": SCOPE, "env": ENV, "reason": str(e)}


def _http_json(url, data=None, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise AnalyticsError(f"Google returned HTTP {e.code} for {urllib.parse.urlparse(url).netloc}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise AnalyticsError(f"request to {urllib.parse.urlparse(url).netloc} failed: {type(e).__name__}") from None


def _access_token(_http):
    with _lock:
        if _tok["value"] and time.time() < _tok["exp"] - 60:
            return _tok["value"]
        g = _grant()
        r = _http(TOKEN_URI, data={"client_id": g["client_id"], "client_secret": g["client_secret"],
                                   "refresh_token": g["refresh_token"], "grant_type": "refresh_token"})
        if not isinstance(r, dict) or not r.get("access_token"):
            raise AnalyticsError("token refresh returned no access token; the owner may need to sign in again")
        _tok["value"] = r["access_token"]
        _tok["exp"] = time.time() + float(r.get("expires_in") or 3600)
        return _tok["value"]


def _day(s, label):
    try:
        return _dt.date.fromisoformat(str(s))
    except ValueError:
        raise InputError(f"{label} must be a date like 2026-11-06") from None


def video_report(video_id, start, end, by_day=False, _http=None):
    """Metrics for one video over Pacific dates start..end inclusive.

    status 'observed' = Google returned rows; 'missing' = no processed rows
    (not converted to zero). channel==MINE: the grant's own channel only.
    """
    if not re.fullmatch(r"[\w-]{11}", video_id or ""):
        raise InputError("video_id must be an 11-character YouTube video ID")
    a, b = _day(start, "start"), _day(end, "end")
    if b < a:
        raise InputError("end is before start")
    if (b - a).days > MAX_DAYS:
        raise InputError(f"at most {MAX_DAYS} days per request")
    _http = _http or _http_json
    params = {"ids": "channel==MINE", "startDate": a.isoformat(), "endDate": b.isoformat(),
              "metrics": ",".join(METRICS), "filters": "video==" + video_id}
    if by_day:
        params.update(dimensions="day", sort="day")
    data = _http(REPORTS + "?" + urllib.parse.urlencode(params), token=_access_token(_http))
    if not isinstance(data, dict):
        raise AnalyticsError("YouTube Analytics returned an unsupported response")
    cols = [h.get("name") for h in data.get("columnHeaders") or []]
    rows = [dict(zip(cols, r)) for r in data.get("rows") or []]
    out = {"video_id": video_id, "start": a.isoformat(), "end": b.isoformat(), "timezone": "America/Los_Angeles",
           "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
           "note": "Google's processed days; the last 1-3 days may be incomplete"}
    if not rows:
        return {**out, "status": "missing", "reason": "no processed rows (not counted as zero)"}
    if by_day:
        return {**out, "status": "observed", "days": rows}
    return {**out, "status": "observed", "metrics": rows[0]}
