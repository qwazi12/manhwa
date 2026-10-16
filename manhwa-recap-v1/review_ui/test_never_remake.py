"""Never make the same chapter twice by accident (owner, 2026-10-16 — third report)."""
import json
import os
import time

import pytest

import autopilot as ap
import ingest
import server
import watchlist as wl


ASURA = "https://asurascans.com/comics/rtoc-3ec3b16f/"


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(server, "_wl_root", lambda: root)
    monkeypatch.setattr(server, "_yt_root", lambda: os.path.join(root, "_yt"))
    server._CANON_CACHE.update(at=0, data=None)
    server._MADE_CACHE.update(at=0, data=None)
    wl.save(root, {"series": [{"id": "rtoc", "title": "RToC", "tier": "high_upside", "mirrors": [
        {"source": "asura", "label": "Asura", "series_url": ASURA, "series_key": ASURA, "support": "supported",
         "status": "ok", "chapters": ["31", "32", "33", "34", "35"], "latest": "35"},
        {"source": "mangayomi", "label": "MGeko", "series_url": "https://www.mgeko.cc/manga/rtoc",
         "series_key": "mangayomi:mgeko:rtoc", "support": "supported", "status": "ok", "chapters": ["33"]}],
        "preferred_mirror": ASURA}]})
    return root


def _chapter(root, pid, url, ch, finished=True):
    d = os.path.join(root, pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "project.json"), "w") as f:
        json.dump({"id": pid, "url": url, "series": "RToC", "chapter": str(ch)}, f)
    if finished:
        with open(os.path.join(d, "segments.json"), "w") as f:
            f.write("[]")


def test_register_counts_every_form(lib):
    root = lib
    _chapter(root, "rtoc_31", ASURA + "chapter/31", 31)                                      # made
    _chapter(root, "en_rtoc-chapter-32", "https://www.mgeko.cc/reader/en/rtoc-chapter-32-eng-li/", 32)   # mirror name
    os.makedirs(os.path.join(root, "_trash", "rtoc_33__1"), exist_ok=True)                    # in the bin
    _chapter(os.path.join(root, "_trash"), "rtoc_33__1", ASURA + "chapter/33", 33)
    with open(os.path.join(root, "_gone.json"), "w") as f:                                    # cleaned up
        json.dump({"rtoc_34": {"url": ASURA + "chapter/34", "series": "RToC", "chapter": "34", "at": 1}}, f)
    _chapter(root, "rtoc_35", ASURA + "chapter/35", 35, finished=False)                       # failed half-way
    reg = server._made_registry(max_age=0)["rtoc"]
    assert set(reg) == {"31", "32", "33", "34"} and "35" not in reg
    assert "Deleted" in reg["33"]


def test_autopilot_skips_chapters_made_in_any_form(lib):
    root = lib
    _chapter(os.path.join(root, "_trash"), "rtoc_33__1", ASURA + "chapter/33", 33)      # deleted
    with open(os.path.join(root, "_gone.json"), "w") as f:                               # cleaned up after posting
        json.dump({"rtoc_34": {"url": ASURA + "chapter/34", "series": "RToC", "chapter": "34", "at": 1,
                               "posted": True}}, f)
    view = server._ap_deps()["view"]()
    st = ap.load(root)
    st["start_from"]["rtoc"] = "31"
    row = ap.candidates(st, view, lambda k: k, set(), time.time())[0]
    assert row["remaining"] == ["31", "32", "35"]          # 33 (in Deleted) and 34 (cleaned up) never again


def test_a_manual_make_of_a_made_chapter_is_refused_unless_on_purpose(lib):
    root = lib
    _chapter(root, "en_rtoc-chapter-33", "https://www.mgeko.cc/reader/en/rtoc-chapter-33-eng-li/", 33)
    with pytest.raises(server.HTTPException) as e:
        server.start_ingest(server.IngestIn(url=ASURA + "chapter/33"))
    assert e.value.status_code == 409 and "already made" in e.value.detail
    assert server._already_made(ASURA + "chapter/34") is None
