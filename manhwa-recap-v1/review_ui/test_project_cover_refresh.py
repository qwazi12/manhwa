"""Owner, 2026-11-07: three queued thumbnails were rebuilt from Asura's share card
(title + "★ RATING"), cached in the chapter before the real-cover fix. A cached
landscape "cover" is replaced by the real one; a portrait cover is kept.
No network. Run: python3 -m pytest test_project_cover_refresh.py -q"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
from PIL import Image  # noqa: E402


def _setup(tmp_path, monkeypatch, cached_size):
    import server
    import thumbnail_studio as ts
    wl = tmp_path / "wl"
    (wl / "_covers").mkdir(parents=True)
    Image.new("RGB", (720, 1030), (200, 30, 30)).save(wl / "_covers" / "fog-land.jpg")      # the real cover
    pdir = tmp_path / "proj" / "fog-land_ch46"
    (pdir / "pages").mkdir(parents=True)
    json.dump({"series_id": "fog-land", "series": "Fog Land"}, open(pdir / "project.json", "w"))
    Image.new("RGB", cached_size, (10, 10, 10)).save(pdir / "pages" / f"{ts.COVER_NAME}.jpg")
    monkeypatch.setattr(server, "_wl_root", lambda: str(wl))
    monkeypatch.setattr(server, "api_watchlist_cover", lambda sid: None)
    import watchlist
    monkeypatch.setattr(watchlist, "load", lambda root: {"series": [{"id": "fog-land", "title": "Fog Land"}]})
    monkeypatch.setattr(watchlist, "find_by_mirror", lambda wl, url: (None, None))
    monkeypatch.setattr(watchlist, "find", lambda wl, sid: {"id": "fog-land", "title": "Fog Land"} if sid == "fog-land" else None)
    return server, str(pdir)


def test_a_cached_share_card_is_replaced_by_the_real_cover(tmp_path, monkeypatch):
    server, pdir = _setup(tmp_path, monkeypatch, (1200, 630))
    out = server._ensure_project_cover(pdir)
    assert out and Image.open(out).size == (720, 1030)


def test_a_real_cached_cover_is_kept(tmp_path, monkeypatch):
    server, pdir = _setup(tmp_path, monkeypatch, (480, 540))
    out = server._ensure_project_cover(pdir)
    assert Image.open(out).size == (480, 540)
