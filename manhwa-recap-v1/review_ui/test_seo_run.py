"""One-click live SEO (owner, 2026-10-06). The research/model step is stubbed
(rule 12: no real calls from tests); the fill-in path is real.
Run: python3 -m pytest test_seo_run.py -q"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)


def test_seo_button_researches_then_fills_title_description_and_tags(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    import seo as _seo
    ing.PROJECTS = str(tmp_path)
    pid = "a-regressors-tale-of-cultivation_31"
    d = tmp_path / pid
    (d / "exports").mkdir(parents=True)
    (d / "exports" / "final_31.mp4").write_bytes(b"\0" * 10)
    (d / "project.json").write_text(json.dumps({"series": "A Regressors Tale Of Cultivation", "chapter": "31",
        "url": "https://asurascans.com/comics/a-regressors-tale-of-cultivation-3ec3b16f/chapter/31"}))
    calls = []

    def fake_generate(body):
        calls.append(body.refresh_style)
        hook = "He Learns Regression Can't Save Them"
        _seo.put(str(d), body.name, {
            "titles": [{"text": "x", "hook": hook, "recommended": True}],
            "summary": "Seo Eunhyun wins at the cost of seven disciples and learns his regression cannot bring them back.",
            "description": "long text", "tags": ["rtoc", "seo eunhyun"], "hashtags": ["#manhwa", "#rtoc"],
            "sources": {"web": {"label": "Web research (Google)", "detail": "7 sources"},
                        "channel": {"label": "Flamingo Remix", "detail": "50 uploads measured"},
                        "research": {"label": "YouTube search", "detail": "10 comparable videos"},
                        "series_youtube": {"label": "YouTube search for this series", "detail": "8 videos"}},
            "applied": {}})
        # the title the real generator builds from the hook (B1 format)
        rec = _seo.get(str(d), body.name)
        import chapter_title as ct
        rec["titles"][0]["text"] = ct.build(hook, "A Regressors Tale Of Cultivation", "31")
        _seo.put(str(d), body.name, rec)
    monkeypatch.setattr(srv, "api_seo_generate", fake_generate)
    r = srv.api_seo_run(srv.SeoGenIn(project=pid, name="final_31.mp4"))
    assert calls == [True]                                   # live: research re-fetched, not cached
    assert r["filled"] == ["title", "description", "tags"], r.get("failed")
    md = r["metadata"]
    assert md["title"] == "He Learns Regression Can't Save Them - A Regressors Tale Of Cultivation Chapter 31 Manhwa Recap"
    blocks = md["description"].split("\n\n")
    assert blocks[0].startswith("He Learns Regression Can't Save Them | A Regressors Tale Of Cultivation Chapter 31")
    assert blocks[1].startswith("Seo Eunhyun wins") and blocks[-1].startswith("#manhwa")
    assert md["tags"][:2] == ["rtoc", "seo eunhyun"]
    assert r["ran"]["web"]["detail"] == "7 sources" and r["problems"] == []
    saved = json.loads((d / "publish.json").read_text())["final_31.mp4"]
    assert saved["title"] == md["title"]                     # persisted, not just returned
