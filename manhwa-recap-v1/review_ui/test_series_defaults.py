"""⚡ Save as series defaults (owner, 2026-10-06: Internal Server Error).
Run: python3 -m pytest test_series_defaults.py -q"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)
URL = "https://asurascans.com/comics/{s}-3ec3b16f/chapter/{n}"


def _ch(root, slug, series, n, md, posted=False):
    d = os.path.join(root, f"{slug}_{n}")
    os.makedirs(os.path.join(d, "exports"))
    open(os.path.join(d, "exports", f"final_{n}.mp4"), "wb").write(b"\0")
    json.dump({"series": series, "chapter": str(n), "url": URL.format(s=slug, n=n)}, open(os.path.join(d, "project.json"), "w"))
    json.dump({f"final_{n}.mp4": md}, open(os.path.join(d, "publish.json"), "w"))
    if posted:
        json.dump({f"final_{n}.mp4": {"results": [{"status": "published", "network": "youtube"}]}},
                  open(os.path.join(d, "publishes.json"), "w"))
    return d


def test_save_as_series_defaults_works_and_never_copies_one_chapters_words(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    ing.PROJECTS = str(tmp_path)
    S, slug = "A Regressors Tale Of Cultivation", "a-regressors-tale-of-cultivation"
    t31 = "Seven Disciples Fall - A Regressors Tale Of Cultivation Chapter 31 Manhwa Recap"
    d31 = _ch(str(tmp_path), slug, S, 31, {"title": t31, "description": "ch31 summary", "tags": ["seo eunhyun"],
                                          "privacy": "public"})
    t30 = "posted title"
    d30 = _ch(str(tmp_path), slug, S, 30, {"title": t30, "description": "d30", "tags": ["x"], "privacy": "public"},
              posted=True)
    _ch(str(tmp_path), "murim-psychopath", "Murim Psychopath", 45, {"title": "other", "tags": ["m"]})
    _ch(str(tmp_path), slug, S, 32, {"title": "He Returns - A Regressors Tale Of Cultivation Chapter 32 Manhwa Recap"})
    redraws = []
    monkeypatch.setattr(srv, "_rerender_chosen_thumbnails", lambda **kw: redraws.append(kw))
    md = {"title": "He Returns - A Regressors Tale Of Cultivation Chapter 32 Manhwa Recap",
          "description": "ch32 summary", "tags": ["rtoc", "cultivation manhwa"],
          "playlist": "PL-rtoc", "privacy": "private", "targets": ["mk:youtube"]}
    r = srv.api_publish_series_defaults(srv.SeriesDefaultsIn(project=f"{slug}_32", name="final_32.mp4", metadata=md))
    assert r["ok"]                                                    # was a 500 (get_all_concepts)
    p31 = json.load(open(os.path.join(d31, "publish.json")))["final_31.mp4"]
    assert p31["title"] == t31 and p31["description"] == "ch31 summary"     # its own words
    assert p31["privacy"] == "private" and p31["playlist"] == "PL-rtoc" and p31["targets"] == ["mk:youtube"]
    assert p31["tags"][0] == "seo eunhyun" and "rtoc" in p31["tags"]       # merged, not replaced
    p30 = json.load(open(os.path.join(d30, "publish.json")))["final_30.mp4"]
    assert p30 == {"title": t30, "description": "d30", "tags": ["x"], "privacy": "public"}   # posted: untouched
    other = json.load(open(os.path.join(str(tmp_path), "murim-psychopath_45", "publish.json")))["final_45.mp4"]
    assert other == {"title": "other", "tags": ["m"]}                   # another series: untouched
    assert redraws == [{"force_cover_default": True, "only_series": slug, "skip_posted": True}]
    assert "title_template" not in r["pack"] and "description_template" not in r["pack"]
    # a NEW chapter's default title is built from its own hook, not ch.32's
    _ch(str(tmp_path), slug, S, 33, {})
    assert "He Returns" not in srv.publish_defaults(os.path.join(str(tmp_path), f"{slug}_33"))["title"]
