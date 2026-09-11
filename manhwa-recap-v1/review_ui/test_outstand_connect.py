"""Phase C tests — Outstand account connection (Session 28).

No OUTSTAND_API_KEY / OUTSTAND_ORG_ID exist on this deployment, so nothing here
has touched the live service. Every request is driven through an injected HTTP
layer against the documented shapes.

Run: python3 test_outstand_connect.py
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
import outstand as osd
import server as srv

CFG = {"configured": True, "api_key": "KEY", "org_id": "ORG",
       "redirect_uri": "https://app.test/api/outstand/callback",
       "missing": [], "api_base": osd.API_BASE, "networks": list(osd.NETWORKS)}


def _clear_env():
    # any casing — the helper must not fall into the very trap under test
    for k in [k for k in os.environ if k.upper().startswith("OUTSTAND")]:
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


def _err(fn, *a, **kw):
    try:
        fn(*a, **kw); return None
    except Exception as e:
        return getattr(e, "status_code", type(e).__name__)


def main():
    r = []
    root = tempfile.mkdtemp(prefix="osd_")
    ing.PROJECTS = root
    _clear_env()

    # ---- config
    c = osd.config()
    r.append(("unconfigured is detected", c["configured"] is False))
    r.append(("...naming exactly what is missing",
              set(c["missing"]) == {"OUTSTAND_API_KEY", "OUTSTAND_ORG_ID",
                                    "OUTSTAND_REDIRECT_URI"}))
    r.append(("youtube is among the supported networks", "youtube" in osd.NETWORKS))
    r.append(("the model is not hardcoded to one network", len(osd.NETWORKS) > 5))

    # ---- capitalisation must not decide whether a key "exists".
    # Env vars are case-sensitive on Linux and this project already mixes
    # conventions (Claude_API_KEY beside GEMINI_API_KEY), so an exact-case
    # lookup reported "not configured" for a key that was set — indistinguishable
    # from never setting it.
    _clear_env()
    os.environ["Outstand_API_KEY"] = "k"
    os.environ["outstand_org_id"] = "o"
    os.environ["OUTSTAND_REDIRECT_URI"] = "https://x/cb"
    c2 = osd.config()
    r.append(("a mixed-case API key is found", bool(c2["api_key"])))
    r.append(("a lowercase org id is found", bool(c2["org_id"])))
    r.append(("...so the integration reads as configured", c2["configured"] is True))
    _clear_env()
    r.append(("with nothing set it still reports all three missing",
              len(osd.config()["missing"]) == 3))

    # ---- connect url
    r.append(("a connect URL cannot be built while unconfigured",
              _err(osd.connect_url, "youtube") == "OutstandError"))
    u = osd.connect_url("youtube", "ST8", CFG)
    parsed = urllib.parse.urlparse(u)
    q = urllib.parse.parse_qs(parsed.query)
    r.append(("the connect URL targets Outstand's socials endpoint",
              u.startswith(osd.CONNECT_BASE + "/youtube/ORG")))
    r.append(("...passes our callback as redirect_uri",
              q["redirect_uri"][0].startswith(CFG["redirect_uri"])))
    r.append(("...carries our CSRF state through that redirect",
              "state=ST8" in q["redirect_uri"][0]))
    r.append(("an unknown network is refused",
              _err(osd.connect_url, "myspace", None, CFG) == "OutstandError"))

    # ---- CSRF state
    st = osd.new_state(root)
    r.append(("a state token verifies once", osd.check_state(root, st) is True))
    r.append(("...and cannot be replayed", osd.check_state(root, st) is False))
    st2 = osd.new_state(root)
    rec = json.load(open(os.path.join(root, osd.STATE_FILE)))
    rec["at"] = time.time() - osd.STATE_TTL - 5
    json.dump(rec, open(os.path.join(root, osd.STATE_FILE), "w"))
    r.append(("an expired state is refused", osd.check_state(root, st2) is False))

    # ---- transport carries the bearer key
    calls = []
    def http(method, url, body, cfg):
        calls.append({"method": method, "url": url, "body": body,
                      "key": cfg["api_key"]})
        if url.endswith("/social-accounts"):
            return [{"id": "acc_1", "network": "youtube", "username": "flamingoremix",
                     "nickname": "Flamingo Remix"},
                    {"id": "acc_2", "network": "x", "username": "someone"}]
        if url.endswith("/posts/") or "/posts/" in url:
            return {"success": True, "post": {
                "id": "9dyJS", "publishedAt": "2026-01-15T10:30:00Z",
                "socialAccounts": [
                    {"id": "acc_1", "network": "youtube", "username": "flamingoremix",
                     "status": "published", "error": None,
                     "platformPostId": "yt_abc",
                     "publishedAt": "2026-01-15T10:30:02Z"},
                    {"id": "acc_2", "network": "x", "username": "someone",
                     "status": "failed", "error": "rate limited",
                     "platformPostId": None, "publishedAt": None}]}}
        if url.endswith("/media/upload"):
            return {"id": "med_1", "upload_url": "https://up.test/put"}
        return {}
    accts = osd.list_accounts(CFG, _http=http)
    r.append(("listing accounts uses the documented endpoint",
              calls[-1]["url"] == osd.API_BASE + "/social-accounts"))
    r.append(("...authenticated with the API key", calls[-1]["key"] == "KEY"))
    r.append(("...and is normalised to account_id/network/username",
              accts[0]["account_id"] == "acc_1" and accts[0]["network"] == "youtube"))

    # ---- sync makes Outstand the source of truth
    osd.sync_accounts(root, CFG, _http=http)
    stored = osd.load_accounts(root)
    r.append(("syncing stores every remote account", set(stored) == {"acc_1", "acc_2"}))
    r.append(("...marked active", all(a["active"] for a in stored.values())))
    def http_one(method, url, body, cfg):
        return [{"id": "acc_1", "network": "youtube", "username": "flamingoremix"}]
    osd.sync_accounts(root, CFG, _http=http_one)
    stored = osd.load_accounts(root)
    r.append(("an account revoked at Outstand goes INACTIVE locally",
              stored["acc_2"]["active"] is False))
    r.append(("...and is not silently deleted", "acc_2" in stored))

    # ---- manual record / remove
    osd.record_connection(root, "acc_3", network="youtube", username="third")
    r.append(("a callback records the connected account",
              osd.load_accounts(root)["acc_3"]["username"] == "third"))
    r.append(("a callback with no account_id is refused",
              _err(osd.record_connection, root, "") == "OutstandError"))
    r.append(("removing an account works", osd.remove_account(root, "acc_3") is True))
    r.append(("removing an unknown account reports false",
              osd.remove_account(root, "nope") is False))

    # ---- status
    _clear_env()
    stt = osd.accounts_status(root)
    r.append(("unconfigured status is its own state", stt["state"] == "not_configured"))
    r.append(("...and cannot publish", stt["can_publish"] is False))
    os.environ["OUTSTAND_API_KEY"] = "KEY"
    os.environ["OUTSTAND_ORG_ID"] = "ORG"
    os.environ["OUTSTAND_REDIRECT_URI"] = CFG["redirect_uri"]
    stt = osd.accounts_status(root)
    r.append(("with an active account it reports connected", stt["state"] == "connected"))
    r.append(("...counting only active ones", stt["n_active"] == 1))
    r.append(("status NEVER returns the API key",
              "api_key" not in json.dumps(stt)))

    # ---- publish request shape
    res = osd.create_post([{"content": "hello"}], ["acc_1"],
                          scheduled_at="2026-10-01T09:00:00Z",
                          youtube=osd.build_youtube_config({"title": "T"}),
                          cfg=CFG, _http=http)
    body = calls[-1]["body"]
    r.append(("publishing posts to /posts/", calls[-1]["url"].endswith("/posts/")))
    r.append(("...with containers and accounts",
              body["containers"][0]["content"] == "hello" and body["accounts"] == ["acc_1"]))
    r.append(("...and scheduledAt when given",
              body["scheduledAt"] == "2026-10-01T09:00:00Z"))
    r.append(("...returning the post id from the documented envelope",
              res["post_id"] == "9dyJS"))
    r.append(("...and the youtube config as a TOP-LEVEL key",
              body["youtube"]["privacyStatus"] == "private"))
    r.append(("a post with no accounts is refused",
              _err(osd.create_post, [{"content": "x"}], []) == "OutstandError"))
    r.append(("a post with no containers is refused",
              _err(osd.create_post, [], ["acc_1"]) == "OutstandError"))
    r.append(("a youtube config without privacyStatus is refused outright",
              _err(osd.create_post, [{"content": "x"}], ["acc_1"], None,
                   {"title": "no privacy"}, CFG, http) == "OutstandError"))

    # ---- the YouTube mapping, per outstand.so/docs/configurations/youtube
    yc = osd.build_youtube_config({})
    r.append(("privacyStatus is ALWAYS set, because the API default is public",
              yc["privacyStatus"] == "private"))
    r.append(("...and defaults to private", yc["privacyStatus"] == "private"))
    r.append(("public is clamped to private in this pass",
              osd.build_youtube_config({"privacy": "public"})["privacyStatus"] == "private"))
    r.append(("an explicit unforced choice is honoured",
              osd.build_youtube_config({"privacy": "unlisted"},
                                       force_private=False)["privacyStatus"] == "unlisted"))
    r.append(("a nonsense privacy falls back to private",
              osd.build_youtube_config({"privacy": "semi"},
                                       force_private=False)["privacyStatus"] == "private"))
    full = osd.build_youtube_config({"title": "My Recap", "tags": ["a", "b"],
                                     "category_id": "24", "made_for_kids": True})
    r.append(("title maps to youtube.title", full["title"] == "My Recap"))
    r.append(("tags map to youtube.tags", full["tags"] == ["a", "b"]))
    r.append(("category maps to youtube.categoryId", full["categoryId"] == "24"))
    r.append(("made-for-kids maps to youtube.madeForKids", full["madeForKids"] is True))
    r.append(("category defaults to 22 when unset",
              osd.build_youtube_config({})["categoryId"] == "22"))
    r.append(("no description field is invented — it is the post content",
              "description" not in full))
    r.append(("no thumbnail or playlist field is invented",
              "thumbnail" not in full and "playlist" not in full))

    # ---- per-account status, not one vague flag
    stat = osd.post_status("9dyJS", CFG, _http=http)
    ids = {x["account_id"]: x for x in stat["results"]}
    r.append(("status is reported PER ACCOUNT", set(ids) == {"acc_1", "acc_2"}))
    r.append(("...with the platform id for the one that worked",
              ids["acc_1"]["platform_post_id"] == "yt_abc"))
    r.append(("...and the failure reason for the one that did not",
              ids["acc_2"]["status"] == "failed" and ids["acc_2"]["error"] == "rate limited"))
    r.append(("a YouTube watch URL is derived from platformPostId, not invented",
              ids["acc_1"]["url"] == "https://www.youtube.com/watch?v=yt_abc"))
    r.append(("a failed account gets no URL", ids["acc_2"]["url"] is None))

    # ---- media
    osd.request_media_upload("v.mp4", "video/mp4", CFG, _http=http)
    r.append(("media upload asks for an upload URL first",
              calls[-1]["url"].endswith("/media/upload")
              and calls[-1]["body"]["content_type"] == "video/mp4"))
    osd.confirm_media("med_1", 1234, CFG, _http=http)
    r.append(("...and confirms with the byte size",
              calls[-1]["url"].endswith("/media/med_1/confirm")
              and calls[-1]["body"]["size"] == 1234))

    # ---- the Phase D gate
    pd = _project(root)
    srv.active_project_dir = lambda: pd
    NAME = "final_a.mp4"
    osd.save_accounts(root, {"acc_1": {"account_id": "acc_1", "network": "youtube",
                                       "username": "flamingoremix", "active": True}})
    el = srv.publish_eligibility(pd, NAME)
    r.append(("an unapproved export cannot publish", el["ready"] is False))
    srv.api_review_save(srv.ReviewIn(project="proj", name=NAME, status="approved"))
    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "T", "privacy": "private"}))
    el = srv.publish_eligibility(pd, NAME)
    r.append(("no target account selected blocks publishing",
              any("at least one connected account" in b for b in el["blockers"])))
    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "T", "privacy": "private",
                                                 "targets": ["acc_1"]}))
    el = srv.publish_eligibility(pd, NAME)
    r.append(("a private, approved, targeted export is eligible", el["ready"] is True))
    r.append(("...with private as the effective visibility",
              el["effective_privacy"] == "private"))
    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "T", "privacy": "public",
                                                 "targets": ["acc_1"]}))
    el = srv.publish_eligibility(pd, NAME)
    r.append(("asking for public is refused without a deliberate override",
              el["ready"] is False
              and any("OUTSTAND_ALLOW_PUBLIC" in b for b in el["blockers"])))
    os.environ["OUTSTAND_ALLOW_PUBLIC"] = "1"
    r.append(("...and allowed once that override is set",
              srv.publish_eligibility(pd, NAME)["ready"] is True))
    os.environ.pop("OUTSTAND_ALLOW_PUBLIC")
    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "T", "privacy": "private",
                                                 "targets": ["acc_1"]}))

    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "T", "privacy": "private",
                                                 "targets": ["gone_acct"]}))
    el = srv.publish_eligibility(pd, NAME)
    r.append(("a target that is no longer connected blocks publishing",
              any("no longer connected" in b for b in el["blockers"])))
    srv.api_publish_save(srv.PublishIn(project="proj", name=NAME,
                                       metadata={"title": "T", "privacy": "private",
                                                 "targets": ["acc_1"]}))

    json.dump([{"seg_index": 0, "panel_id": "CHANGED", "dur": 3.0, "start": 0.0,
                "end": 3.0, "user_included": True, "crop_bbox_norm": None,
                "beats": [{"index": 0, "text": "x", "start": 0.0, "end": 3.0}]}],
              open(os.path.join(pd, "segments.json"), "w"))
    r.append(("a superseded cut blocks publishing",
              any("cut changed" in b for b in srv.publish_eligibility(pd, NAME)["blockers"])))
    json.dump([{"seg_index": 0, "panel_id": "p", "dur": 3.0, "start": 0.0,
                "end": 3.0, "user_included": True, "crop_bbox_norm": None,
                "beats": [{"index": 0, "text": "x", "start": 0.0, "end": 3.0}]}],
              open(os.path.join(pd, "segments.json"), "w"))

    srv.save_publishes(pd, {NAME: {"results": [{"account_id": "acc_1",
                                                "status": "published"}]}})
    el = srv.publish_eligibility(pd, NAME)
    r.append(("an already-published export is blocked from re-publishing",
              any("already published" in b for b in el["blockers"])))
    srv.save_publishes(pd, {})

    # ---- endpoints refuse cleanly while unconfigured
    _clear_env()
    r.append(("connect returns 503 while unconfigured", _err(srv.os_connect) == 503))
    r.append(("refresh returns 503 while unconfigured", _err(srv.os_refresh) == 503))
    r.append(("status still answers", srv.os_status()["state"] == "not_configured"))
    r.append(("disconnecting an unknown account is a 404",
              _err(srv.os_disconnect, srv.OutstandDelIn(account_id="nope")) == 404))

    _clear_env()
    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
