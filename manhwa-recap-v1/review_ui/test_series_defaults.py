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
