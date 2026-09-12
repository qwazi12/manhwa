"""YouTube Data API v3 client — the only place this app talks to YouTube.

Kept separate from the SEO logic so the network surface is one auditable file
with one retry policy, and so seo.py can be tested with a fake client instead
of the live API.

Auth is the API key in the x-goog-api-key HEADER, never a query parameter:
Google's own guidance, because URLs end up in logs and referrer headers.

Every call is bounded. Quota is 10,000 units/day and search.list alone costs
100 units, so an unbounded research loop could exhaust the day's quota in one
generation. Callers pass explicit maxima and get back whatever was affordable.
"""
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.googleapis.com/youtube/v3"
UA = "ManhwaRecapStudio/1.0 (+https://manhwa.nodepilot.dev)"
TIMEOUT = 30

# Rough quota costs, from Google's published table. Tracked so a generation can
# report what it spent rather than silently eating the daily allowance.
COST = {"/search": 100, "/channels": 1, "/playlistItems": 1, "/videos": 1}


class YouTubeError(Exception):
    """A call failed. The message carries Google's own reason string, because
    'API not enabled', 'referrer restricted' and 'quota exceeded' need
    completely different fixes and a generic failure hides which one it is."""


def api_key():
    # Accept either name: YOUTUBE_API_KEY is what the deployment uses, but a
    # case variant cost this project a whole debugging session once before.
    for k, v in os.environ.items():
        if k.upper() in ("YOUTUBE_API_KEY", "YT_API_KEY") and v:
            return v
    return ""


def configured():
    return bool(api_key())


class Client:
    """Thin typed wrapper. `spent` accumulates quota units for reporting."""

    def __init__(self, key=None, _http=None):
        self.key = key or api_key()
        self._http = _http            # tests inject; production leaves None
        self.spent = 0
        self.calls = []

    def _get(self, path, **params):
        if not self.key:
            raise YouTubeError("no YouTube API key is configured")
        self.spent += COST.get(path, 1)
        self.calls.append(path)
        if self._http is not None:
            return self._http(path, params)
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        url = BASE + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "x-goog-api-key": self.key, "User-Agent": UA})
        last = ""
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                reason, msg = "", body[:200]
                try:
                    err = json.loads(body).get("error", {})
                    msg = err.get("message", msg)
                    reason = (err.get("errors") or [{}])[0].get("reason", "")
                except Exception:
                    pass
                # 403 here is almost never transient: it is the API not being
                # enabled, or a referrer restriction that blocks server calls.
                # Retrying those just wastes time, so only back off on 429/5xx.
                if e.code in (429, 500, 503) and attempt < 2:
                    last = "%s: %s" % (e.code, msg)
                    time.sleep(2 * (attempt + 1))
                    continue
                raise YouTubeError("YouTube %s%s: %s" % (
                    e.code, " (%s)" % reason if reason else "", msg))
            except Exception as e:
                if attempt < 2:
                    last = str(e)[:120]
                    time.sleep(2 * (attempt + 1))
                    continue
                raise YouTubeError("YouTube request failed: %s" % (last or e))
        raise YouTubeError("YouTube request failed after retries: %s" % last)

    # ------------------------------------------------------------- channel
    def channel(self, handle):
        """The channel's own record. Cheap (1 unit) and the anchor for style."""
        h = handle if handle.startswith("@") else "@" + handle
        d = self._get("/channels", part="snippet,statistics,contentDetails",
                      forHandle=h)
        items = d.get("items") or []
        if not items:
            raise YouTubeError("no channel found for handle %s" % h)
        it = items[0]
        sn, st = it.get("snippet", {}), it.get("statistics", {})
        uploads = ((it.get("contentDetails") or {})
                   .get("relatedPlaylists", {}).get("uploads"))
        return {"channel_id": it.get("id"), "title": sn.get("title"),
                "description": sn.get("description", ""),
                "video_count": int(st.get("videoCount") or 0),
                "subscriber_count": int(st.get("subscriberCount") or 0),
                "uploads_playlist": uploads}

    def uploads(self, playlist_id, limit=50):
        """Recent uploads: title + description. 1 unit per page of 50."""
        out, token = [], None
        while len(out) < limit:
            p = {"part": "snippet", "playlistId": playlist_id,
                 "maxResults": min(50, limit - len(out))}
            if token:
                p["pageToken"] = token
            d = self._get("/playlistItems", **p)
            for it in d.get("items", []):
                sn = it.get("snippet", {})
                out.append({"title": sn.get("title", ""),
                            "description": sn.get("description", "") or "",
                            "published_at": sn.get("publishedAt"),
                            "video_id": (sn.get("resourceId") or {}).get("videoId")})
            token = d.get("nextPageToken")
            if not token:
                break
        return out[:limit]

    # ------------------------------------------------------------ research
    def search_recaps(self, query, limit=8):
        """Similar recap videos. 100 units — the expensive call, so callers
        make ONE per generation, not one per keyword."""
        d = self._get("/search", part="snippet", q=query, type="video",
                      maxResults=min(limit, 25), order="relevance",
                      relevanceLanguage="en")
        out = []
        for it in d.get("items", []):
            sn = it.get("snippet", {})
            out.append({"title": sn.get("title", ""),
                        "channel": sn.get("channelTitle", ""),
                        "video_id": (it.get("id") or {}).get("videoId"),
                        "published_at": sn.get("publishedAt")})
        return out

    def video_stats(self, video_ids):
        """View counts, so 'high-performing' is measured rather than assumed.
        1 unit for up to 50 ids."""
        ids = [v for v in video_ids if v][:50]
        if not ids:
            return {}
        d = self._get("/videos", part="statistics,snippet", id=",".join(ids))
        out = {}
        for it in d.get("items", []):
            st = it.get("statistics", {})
            out[it.get("id")] = {
                "views": int(st.get("viewCount") or 0),
                "likes": int(st.get("likeCount") or 0),
                "tags": (it.get("snippet") or {}).get("tags") or [],
            }
        return out
