"""⚡ Save as series defaults (owner, 2026-10-06: Internal Server Error; keep
the behaviour as designed, just make it work). Run: python3 -m pytest test_series_defaults.py -q"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")
os.environ.pop("GEMINI_API_KEY", None)


def test_save_as_series_defaults_no_longer_500s_and_applies_to_the_series(tmp_path, monkeypatch):
    import ingest as ing
    import server as srv
    ing.PROJECTS = str(tmp_path)
    slug, S = "a-regressors-tale-of-cultivation", "A Regressors Tale Of Cultivation"
    for n in (31, 32):
        d = tmp_path / f"{slug}_{n}"
        (d / "exports").mkdir(parents=True)
        (d / "exports" / f"final_{n}.mp4").write_bytes(b"\0")
        (d / "project.json").write_text(json.dumps({"series": S, "chapter": str(n),
            "url": f"https://asurascans.com/comics/{slug}-3ec3b16f/chapter/{n}"}))
        (d / "publish.json").write_text(json.dumps({f"final_{n}.mp4": {"title": "old", "tags": ["old"]}}))
    monkeypatch.setattr(srv, "_rerender_chosen_thumbnails", lambda **kw: None)
    md = {"title": f"He Returns - {S} Chapter 32 Manhwa Recap", "description": "Chapter 32 recap",
          "tags": ["rtoc"], "playlist": "PL-rtoc", "privacy": "private", "targets": ["mk:youtube"]}
    r = srv.api_publish_series_defaults(srv.SeriesDefaultsIn(project=f"{slug}_32", name="final_32.mp4", metadata=md))
    assert r["ok"] and r["applied"] == 2
    p31 = json.loads((tmp_path / f"{slug}_31" / "publish.json").read_text())["final_31.mp4"]
    assert p31["title"] == f"He Returns - {S} Chapter 31 Manhwa Recap"
    assert p31["description"] == "Chapter 31 recap"
    assert p31["tags"] == ["rtoc"] and p31["playlist"] == "PL-rtoc" and p31["privacy"] == "private"


def test_a_new_chapter_keeps_the_series_title_with_its_own_number(tmp_path, monkeypatch):
    """Owner, 2026-10-07: same title for every chapter, only the number changes —
    the automatic SEO fill-in after a render must not replace it."""
    import ingest as ing
    import server as srv
    import seo as _seo
    ing.PROJECTS = str(tmp_path)
    slug, S = "revenge-of-the-iron-blooded-sword-hound", "Revenge Of The Iron Blooded Sword Hound"
    for n in (183, 184):
        d = tmp_path / f"{slug}_{n}"
        (d / "exports").mkdir(parents=True)
        (d / "exports" / f"final_{n}.mp4").write_bytes(b"\0")
        (d / "project.json").write_text(json.dumps({"series": S, "chapter": str(n),
            "url": f"https://asurascans.com/comics/{slug}-3ec3b16f/chapter/{n}"}))
    (tmp_path / f"{slug}_183" / "publish.json").write_text(json.dumps({"final_183.mp4": {}}))
    monkeypatch.setattr(srv, "_rerender_chosen_thumbnails", lambda **kw: None)
    t = "**NEW PART** Abandoned By His Clan, He Unlocks A Deadly DEMONIC Power"
    srv.api_publish_series_defaults(srv.SeriesDefaultsIn(project=f"{slug}_183", name="final_183.mp4", metadata={
        "title": t, "description": "The Baskerville saga continues.", "tags": ["vikir", "baskerville"],
        "category_id": "24", "made_for_kids": False, "synthetic_disclosure": True, "privacy": "public"}))
    # ch.184 is rendered later; SEO would normally fill title/description/tags
    d184 = tmp_path / f"{slug}_184"
    _seo.put(str(d184), "final_184.mp4", {"titles": [{"text": "An SEO Title", "recommended": True}],
                                           "description": "seo desc", "tags": ["seo"], "hashtags": []})
    monkeypatch.setattr(srv, "api_thumbcopilot_generate", lambda b: None)
    srv._prepare_publish(str(d184), "final_184.mp4")
    md = {**srv.publish_defaults(str(d184)), **(srv.load_publish(str(d184)).get("final_184.mp4") or {})}
    assert md["title"] == t + " Ch. 184"                     # same title, its own number
    assert md["description"] == "The Baskerville saga continues." and "vikir" in md["tags"] and "seo" not in md["tags"]
    assert md["category_id"] == "24" and md["synthetic_disclosure"] is True and md["privacy"] == "public"
    p183 = json.loads((tmp_path / f"{slug}_183" / "publish.json").read_text())["final_183.mp4"]
    assert p183["title"] == t + " Ch. 183" and p183["category_id"] == "24"
    # and the two never count as duplicates
    import chapter_title as ct
    assert not [p for p in ct.problems(md["title"], S, "184", [p183["title"]]) if "already" in p]
