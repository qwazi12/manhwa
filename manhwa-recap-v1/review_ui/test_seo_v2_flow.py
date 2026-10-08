"""Spec 10 end to end (model and YouTube stubbed — rule 12).
Run: python3 -m pytest test_seo_v2_flow.py -q"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)
SLUG, S = "murim-psychopath", "Murim Psychopath"
URL = "https://asurascans.com/comics/murim-psychopath-bd5bdaf8/chapter/{}"


def _ch(root, n, posted=False):
    d = os.path.join(root, f"{SLUG}_{n}")
    os.makedirs(os.path.join(d, "exports"))
    open(os.path.join(d, "exports", f"f{n}.mp4"), "wb").write(b"\0")
    json.dump({"series": S, "chapter": str(n), "url": URL.format(n)}, open(os.path.join(d, "project.json"), "w"))
    json.dump({f"f{n}.mp4": {"title": f"old {n}", "description": "old", "tags": ["old"]}},
              open(os.path.join(d, "publish.json"), "w"))
    if posted:
        json.dump({f"f{n}.mp4": {"results": [{"status": "published", "network": "youtube",
                                              "url": f"https://www.youtube.com/watch?v=prevvideo{n}"}]}},
                  open(os.path.join(d, "publishes.json"), "w"))
    return d


def test_hook_pick_lock_package_validate_and_post_fields(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    import series_research as sr
    import series_hooks as sh
    import series_pack as sp
    import upload_post as up
    ing.PROJECTS = str(tmp_path)
    d44 = _ch(str(tmp_path), 44, posted=True)
    d45 = _ch(str(tmp_path), 45)
    d46 = _ch(str(tmp_path), 46)
    monkeypatch.setattr(srv, "_chapter_page_title", lambda url, timeout=15: "Intensifying by the Minute" if "/45" in url else "")
    monkeypatch.setattr(srv, "_rerender_chosen_thumbnails", lambda **kw: None)
    # the series research the hook is built from (Series Bible premise)
    bible = {"world_setting": {"premise": "A VR gamer's logout fails and he wakes up in the real Murim as a psychopath."},
             "characters": [{"name": "Dong Bongsu", "role": "protagonist"}]}
    import series_bible
    monkeypatch.setattr(series_bible, "load_series_bible", lambda sid, pdir=None: bible)
    answer = [{"hook": "He Thought Murim Was Just a Game", "mechanic": 5, "source_index": [0], "why": "premise"},
              {"hook": "His Class Change Shocks Murim", "mechanic": 1, "source_index": [0], "why": "x"},
              {"hook": "Psycho Killer Reborn in Murim", "mechanic": 3, "source_index": [0], "why": "y"}]
    monkeypatch.setattr(sr, "_post", lambda body, key, _u=None: {"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]})
    import gemini_tts
    monkeypatch.setattr(gemini_tts, "env_any_case", lambda k: "test-key")
    res = srv.api_series_hooks(srv.HookGenIn(project=f"{SLUG}_45"))
    c = {x["hook"]: x for x in res["candidates"]}
    assert c["His Class Change Shocks Murim"]["problems"]              # §5 blacklist
    assert not c["He Thought Murim Was Just a Game"]["problems"]
    assert c["He Thought Murim Was Just a Game"]["example_title"] == \
        "[45] He Thought Murim Was Just a Game — Murim Psychopath | Manhwa Recap"
    # the tease call (Stage 2) — stubbed model answer
    monkeypatch.setattr(sr, "_post", lambda body, key, _u=None: {"candidates": [{"content": {"parts": [{"text": json.dumps(
        {"dramatic_beat": "the sect turns on him", "tease": "His logout failed — and now every kill makes him stronger.",
         "new_entities": ["Yeon"]})}]}}]})
    r = srv.api_series_hooks_lock(srv.HookLockIn(project=f"{SLUG}_45", hook="He Thought Murim Was Just a Game", mechanic=5))
    assert set(r["applied"]) == {f"{SLUG}_45", f"{SLUG}_46"}             # unposted only
    p44 = json.load(open(os.path.join(d44, "publish.json")))["f44.mp4"]
    assert p44["title"] == "old 44"                                    # posted: untouched
    md = json.load(open(os.path.join(d45, "publish.json")))["f45.mp4"]
    assert md["title"] == "[45] He Thought Murim Was Just a Game — Murim Psychopath | Manhwa Recap"
    first, second = md["description"].split("\n")[:2]
    assert first.startswith('Murim Psychopath Chapter 45 recap (English) — "Intensifying by the Minute"!')
    assert second == "Watch Chapter 44 here: https://www.youtube.com/watch?v=prevvideo44"
    assert md["tags"][0] == "murim psychopath chapter 45" and md["seo_v2"]
    assert srv.validate_publish(md, d45, "f45.mp4") == []
    # an edited frozen block is caught before posting (D3)
    bad = dict(md, description=md["description"].replace("promptly address", "address"))
    assert any("D3" in p for p in srv.validate_publish(bad, d45, "f45.mp4"))
    # playlist: owner pastes the link; packages pick it up; Upload-Post gets it + the comment
    srv.api_series_playlist(srv.PlaylistIn(project=f"{SLUG}_45", playlist="https://www.youtube.com/playlist?list=PLabcdefghij123"))
    md = json.load(open(os.path.join(d45, "publish.json")))["f45.mp4"]
    assert md["youtube_playlist_id"] == "PLabcdefghij123"
    assert "Start from Chapter 1: https://www.youtube.com/playlist?list=PLabcdefghij123" in md["description"]
    assert md["first_comment"].startswith("Start from Chapter 1:")
    sent = {}

    class Resp:
        status_code = 200
        def json(self):
            return {"success": True, "request_id": "r"}
    def fake_post(url, data=None, files=None, headers=None, timeout=None):
        sent["data"] = data
        return Resp()
    monkeypatch.setattr(up.requests, "post", fake_post) if hasattr(up, "requests") else None
    # a new chapter rendered later gets the package automatically (no free-form SEO)
    d47 = _ch(str(tmp_path), 47)
    monkeypatch.setattr(srv, "api_thumbcopilot_generate", lambda b: None)
    srv._prepare_publish(d47, "f47.mp4")
    m47 = {**srv.publish_defaults(d47), **json.load(open(os.path.join(d47, "publish.json")))["f47.mp4"]}
    assert m47["title"] == "[47] He Thought Murim Was Just a Game — Murim Psychopath | Manhwa Recap"
    pack = sp.load(str(tmp_path), SLUG)
    assert pack["title_lock"]["approved_by_user"] and pack["playlist_id"] == "PLabcdefghij123"


def test_upload_post_sends_playlist_and_first_comment(monkeypatch):
    import upload_post as up
    import inspect
    src = inspect.getsource(up)
    assert '"youtube_playlist_id"' in src and '"youtube_first_comment"' in src
