"""Phase C tests — YouTube account connection (Session 28).

No Google OAuth credentials exist on this deployment, so the live handshake has
never run. These tests drive everything that does not need a real secret: config
detection, the authorize URL, the token request shapes (HTTP injected), CSRF
state, storage, status reporting, and the upload gate Phase D will depend on.

Run: python3 test_youtube_connect.py
"""
import json
import os
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import ingest as ing
import server as srv
import youtube as yt

CFG = {"configured": True, "client_id": "cid", "client_secret": "sec",
       "redirect_uri": "https://example.test/api/youtube/callback",
       "missing": [], "scopes": yt.SCOPES}


def _clear_env():
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "OAUTH_REDIRECT_URI",
              "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REDIRECT_URI"):
        os.environ.pop(k, None)


def _project(root, name="proj"):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "exports"), exist_ok=True)
    json.dump([{"seg_index": 0, "panel_id": "p", "dur": 3.0, "start": 0.0,
                "end": 3.0, "user_included": True, "crop_bbox_norm": None,
                "beats": [{"index": 0, "text": "x", "start": 0.0, "end": 3.0}]}],
              open(os.path.join(d, "segments.json"), "w"))
    json.dump({"series": "S", "chapter": "1"}, open(os.path.join(d, "project.json"), "w"))
    open(os.path.join(d, "exports", "final_a.mp4"), "wb").write(b"\0" * 50)
    return d


def main():
    r = []
    root = tempfile.mkdtemp(prefix="yt_")
    ing.PROJECTS = root
    _clear_env()

    # ---- config detection
    cfg = yt.oauth_config()
    r.append(("unconfigured is detected", cfg["configured"] is False))
    r.append(("...and names exactly what is missing",
              set(cfg["missing"]) == {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                                      "OAUTH_REDIRECT_URI"}))
    os.environ["YOUTUBE_CLIENT_ID"] = "x"
    os.environ["YOUTUBE_CLIENT_SECRET"] = "y"
    os.environ["OAUTH_REDIRECT_URI"] = "https://e.test/cb"
    r.append(("YOUTUBE_* names are accepted as well as GOOGLE_*",
              yt.oauth_config()["configured"] is True))
    _clear_env()

    # ---- authorize url
    try:
        yt.authorize_url("s")
        raised = False
    except RuntimeError:
        raised = True
    r.append(("building a consent URL while unconfigured raises", raised))
    url = yt.authorize_url("state123", CFG)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    r.append(("the consent URL targets Google", url.startswith(yt.AUTH_ENDPOINT)))
    r.append(("...carries the state token", q["state"] == ["state123"]))
    r.append(("...requests offline access, or the link dies in an hour",
              q["access_type"] == ["offline"]))
    r.append(("...forces consent so a refresh token is actually issued",
              q["prompt"] == ["consent"]))
    r.append(("...asks for the upload scope",
              "youtube.upload" in q["scope"][0]))

    # ---- CSRF state is single-use and expiring
    st = yt.new_state(root)
    r.append(("a state token verifies once", yt.check_state(root, st) is True))
    r.append(("...and cannot be replayed", yt.check_state(root, st) is False))
    st2 = yt.new_state(root)
    r.append(("a mismatched state is refused", yt.check_state(root, "wrong") is False))
    st3 = yt.new_state(root)
    rec = json.load(open(os.path.join(root, yt.STATE_FILE)))
    rec["at"] = time.time() - yt.STATE_TTL - 5
    json.dump(rec, open(os.path.join(root, yt.STATE_FILE), "w"))
    r.append(("an expired state is refused", yt.check_state(root, st3) is False))

    # ---- token requests (HTTP injected; never hits Google)
    seen = {}
    def fake_post(url, fields):
        seen["url"], seen["fields"] = url, fields
        return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
                "scope": yt.SCOPES, "token_type": "Bearer"}
    tok = yt.exchange_code("CODE", CFG, _http=fake_post)
    r.append(("the code exchange posts to Google's token endpoint",
              seen["url"] == yt.TOKEN_ENDPOINT))
    r.append(("...as an authorization_code grant",
              seen["fields"]["grant_type"] == "authorization_code"
              and seen["fields"]["code"] == "CODE"))
    yt.refresh_access_token("RT", CFG, _http=fake_post)
    r.append(("a refresh uses the refresh_token grant",
              seen["fields"]["grant_type"] == "refresh_token"))

    # ---- storage
    rec = yt.store_tokens(root, tok, {"channel_id": "UC1", "channel_title": "My Channel"})
    r.append(("tokens persist with the channel identity",
              rec["channel_title"] == "My Channel" and rec["refresh_token"] == "RT"))
    r.append(("the account file is owner-only",
              (os.stat(os.path.join(root, yt.ACCOUNT_FILE)).st_mode & 0o077) == 0))
    later = yt.store_tokens(root, {"access_token": "AT2", "expires_in": 3600})
    r.append(("a later response WITHOUT a refresh token keeps the existing one",
              later["refresh_token"] == "RT"))

    # ---- status reporting
    os.environ["GOOGLE_CLIENT_ID"] = "cid"
    os.environ["GOOGLE_CLIENT_SECRET"] = "sec"
    os.environ["OAUTH_REDIRECT_URI"] = "https://e.test/cb"
    stt = yt.account_status(root)
    r.append(("a stored account reports connected", stt["state"] == "connected"))
    r.append(("...and upload-capable", stt["can_upload"] is True))
    r.append(("status NEVER leaks token material",
              not any(k in stt for k in ("access_token", "refresh_token"))))
    acc = yt.load_account(root); acc.pop("refresh_token"); yt.save_account(root, acc)
    stt = yt.account_status(root)
    r.append(("no refresh token means needs_reauth, not connected",
              stt["state"] == "needs_reauth" and stt["can_upload"] is False))
    yt.clear_account(root)
    r.append(("disconnect removes the account", yt.account_status(root)["state"] == "disconnected"))
    _clear_env()
    r.append(("unconfigured status is its own state, not 'disconnected'",
              yt.account_status(root)["state"] == "not_configured"))

    # ---- the endpoint refuses cleanly while unconfigured
    try:
        srv.yt_connect(); code = None
    except Exception as e:
        code = getattr(e, "status_code", None)
    r.append(("connect returns 503 while unconfigured", code == 503))
    r.append(("status endpoint still answers", srv.yt_status()["state"] == "not_configured"))
    r.append(("disconnect is safe when nothing is connected",
              srv.yt_disconnect()["ok"] is True))

    # ---- the Phase D gate
    pd = _project(root)
    srv.active_project_dir = lambda: pd
    NAME = "final_a.mp4"
    el = srv.upload_eligibility(pd, NAME)
    r.append(("an unapproved export cannot upload", el["ready"] is False))
    r.append(("...and the missing connection is named",
              any("connect" in b.lower() or "credential" in b.lower()
                  for b in el["blockers"])))
    srv.api_review_save(srv.ReviewIn(project="proj", name=NAME, status="approved"))
    el = srv.upload_eligibility(pd, NAME)
    r.append(("approving alone is not enough without an account",
              el["ready"] is False))
    r.append(("...and approval is no longer a blocker",
              not any("not been approved" in b for b in el["blockers"])))

    # connect an account, then check the remaining gates
    os.environ["GOOGLE_CLIENT_ID"] = "cid"
    os.environ["GOOGLE_CLIENT_SECRET"] = "sec"
    os.environ["OAUTH_REDIRECT_URI"] = "https://e.test/cb"
    yt.store_tokens(root, {"access_token": "AT", "refresh_token": "RT",
                           "expires_in": 3600}, {"channel_title": "C"})
    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "T", "privacy": "private"}))
    el = srv.upload_eligibility(pd, NAME)
    r.append(("approved + connected + valid metadata is eligible", el["ready"] is True))

    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "T", "privacy": "public"}))
    el = srv.upload_eligibility(pd, NAME)
    r.append(("a non-private privacy is blocked in this phase", el["ready"] is False))
    r.append(("...saying only private is permitted",
              any("private" in b for b in el["blockers"])))
    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "T", "privacy": "private"}))

    json.dump([{"seg_index": 0, "panel_id": "CHANGED", "dur": 3.0, "start": 0.0,
                "end": 3.0, "user_included": True, "crop_bbox_norm": None,
                "beats": [{"index": 0, "text": "x", "start": 0.0, "end": 3.0}]}],
              open(os.path.join(pd, "segments.json"), "w"))
    el = srv.upload_eligibility(pd, NAME)
    r.append(("a superseded cut blocks upload", el["ready"] is False
              and any("cut changed" in b for b in el["blockers"])))
    json.dump([{"seg_index": 0, "panel_id": "p", "dur": 3.0, "start": 0.0,
                "end": 3.0, "user_included": True, "crop_bbox_norm": None,
                "beats": [{"index": 0, "text": "x", "start": 0.0, "end": 3.0}]}],
              open(os.path.join(pd, "segments.json"), "w"))

    srv.save_uploads(pd, {NAME: {"status": "uploaded", "video_id": "abc123",
                                 "video_url": "https://youtu.be/abc123"}})
    el = srv.upload_eligibility(pd, NAME)
    r.append(("an already-uploaded export is blocked from re-upload",
              el["ready"] is False and any("already uploaded" in b for b in el["blockers"])))
    r.append(("...and the prior upload is surfaced",
              (el["already_uploaded"] or {}).get("video_id") == "abc123"))
    srv.save_uploads(pd, {})

    os.remove(os.path.join(pd, "exports", NAME))
    el = srv.upload_eligibility(pd, NAME)
    r.append(("a deleted export cannot upload", el["ready"] is False))

    _clear_env()
    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
