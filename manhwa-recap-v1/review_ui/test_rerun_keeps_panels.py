"""Owner review, 2026-11-17: re-running one step of Fog Land ch.46 ("record the
voice again") downloaded and cut the chapter again. The cut asks a model where
tall strips divide; the second cut differed from the first in 78 panels, so
the chapter stopped on "Description cache is stale", after $0.04 of cutting,
on a step the site lists as free. A step re-run now keeps the pages and panels
the saved descriptions were made from, and puts an archived cut back when a
later one has replaced it.
No network: every service boundary is faked (test_generation_cache's harness).
Run: python3 -m pytest test_rerun_keeps_panels.py -q"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["AUTOPILOT_SCHEDULER"] = "0"
from test_generation_cache import cache, ingest_fixture, offline  # noqa: E402,F401

URL = "https://asuracomic.net/series/offline-story-12345678/chapter/1"


def _panel(path, colour):
    Image.new("RGB", (200, 200), colour).save(path)


def _describe_all(pdir):
    """descriptions.json for the panels on disk, each recording its source file."""
    recs = [{"panel_id": p.stem, "file": p.name, "width": 200, "height": 200, "ok": True, "role": "art",
             "ocr_text": "Come back.", "visual_description": "A boy waits in a room.",
             "generation": {"version": cache.VERSION, "inputs": {"source_sha256": cache.file_digest(p)}}}
            for p in sorted((pdir / "crops").glob("*.png"))]
    cache.write_json(pdir / "descriptions.json", recs)


@pytest.fixture
def chapter(ingest_fixture, monkeypatch):
    """A chapter made once (through voice), with counters on download and cut."""
    import ingest
    pdir, counts, run, reached = ingest_fixture
    counts.update(download=0, split=0)

    def download(url, pages):
        counts["download"] += 1
        _panel(Path(pages) / "page001.png", "blue")
        return [str(Path(pages) / "page001.png")]

    def split(pages, crops, **kw):
        counts["split"] += 1
        _panel(Path(crops) / "page001_panel_001.png", "blue")
        return 1, {"pages": 1, "panels": 1}

    def describe_subprocess(*a, **kw):
        if not (pdir / "descriptions.json").exists():
            _describe_all(pdir)
        return SimpleNamespace(returncode=0)

    monkeypatch.setitem(sys.modules, "scraper", SimpleNamespace(download_chapter=download, LAST_WARNING=""))
    monkeypatch.setitem(sys.modules, "splitlab", SimpleNamespace(split_into=split))
    monkeypatch.setattr(ingest.subprocess, "run", describe_subprocess)

    def go(keep_source):
        said = []
        with pytest.raises(reached):
            ingest.run_ingest(URL, lambda *a: said.append(a), direct=False, keep_source=keep_source)
        return said

    go(False)
    assert counts == {"narrate": 1, "voice": 1, "download": 1, "split": 1}
    return pdir, counts, go


def _later_cut(pdir):
    """What the failed re-run left behind: a different cut promoted, the described one archived."""
    staging = pdir / ".source-staging-test"
    (staging / "pages").mkdir(parents=True)
    (staging / "crops").mkdir()
    _panel(staging / "pages" / "page001.png", "blue")                 # same page
    _panel(staging / "crops" / "page001_panel_001.png", "red")        # cut differently
    _panel(staging / "crops" / "page001_panel_002.png", "green")
    rev = cache.promote_sources(pdir, staging)
    assert rev and (pdir / rev / "crops" / "page001_panel_001.png").exists()
    return rev


def test_a_step_rerun_does_not_download_or_cut_the_chapter_again(chapter):
    pdir, counts, go = chapter
    said = go(True)
    assert counts == {"narrate": 1, "voice": 1, "download": 1, "split": 1}
    assert any(a[0] == "split" and "kept as they were described" in a[1] for a in said)
    assert not list(pdir.glob(".source-staging-*"))


def test_an_ordinary_run_still_downloads_and_cuts(chapter):
    pdir, counts, go = chapter
    go(False)
    assert (counts["download"], counts["split"]) == (2, 2)


def test_the_described_cut_is_put_back_when_a_later_cut_replaced_it(chapter):
    pdir, counts, go = chapter
    described = cache.file_digest(pdir / "crops" / "page001_panel_001.png")
    rev = _later_cut(pdir)
    assert cache.file_digest(pdir / "crops" / "page001_panel_001.png") != described
    said = go(True)
    assert counts == {"narrate": 1, "voice": 1, "download": 1, "split": 1}          # nothing paid again
    assert sorted(p.name for p in (pdir / "crops").glob("*.png")) == ["page001_panel_001.png"]
    assert cache.file_digest(pdir / "crops" / "page001_panel_001.png") == described
    assert any(a[0] == "split" and os.path.basename(rev) in a[1] for a in said)
    kept = [d for d in (pdir / "_revisions").glob("source-*") if (d / "crops" / "page001_panel_002.png").exists()]
    assert len(kept) == 1                                                              # the later cut is archived, not deleted
    assert json.load(open(kept[0] / "index.json"))["restored_from"] == os.path.basename(rev)


def test_a_later_cut_with_no_matching_archive_is_cut_again_as_before(chapter):
    import shutil
    pdir, counts, go = chapter
    rev = _later_cut(pdir)
    shutil.rmtree(pdir / rev)
    assert cache.described_source(pdir) is None
    go(True)
    assert (counts["download"], counts["split"]) == (2, 2)


@pytest.mark.parametrize("damage", ["extra panel", "changed panel", "no recorded source", "no descriptions", "no pages"])
def test_panels_are_kept_only_when_every_one_is_the_file_that_was_described(chapter, damage):
    import shutil
    pdir, counts, go = chapter
    assert cache.described_source(pdir) == {"restored": None}
    if damage == "extra panel":
        _panel(pdir / "crops" / "page001_panel_002.png", "green")
    elif damage == "changed panel":
        _panel(pdir / "crops" / "page001_panel_001.png", "red")
    elif damage == "no recorded source":
        recs = json.load(open(pdir / "descriptions.json"))
        recs[0].pop("generation")
        cache.write_json(pdir / "descriptions.json", recs)
    elif damage == "no descriptions":
        os.remove(pdir / "descriptions.json")
    else:
        shutil.rmtree(pdir / "pages")
    assert cache.described_source(pdir) is None


def test_only_a_step_rerun_asks_to_keep_the_panels(monkeypatch):
    import ingest
    import server
    got = {}

    def fake_run(url, progress, job_id=None, **kw):
        got[job_id] = kw.get("keep_source")
        return {"id": None}
    for name in ("_persist_ingest", "_load_ingest", "_after_ingest"):
        monkeypatch.setattr(server, name, lambda j: None)
    monkeypatch.setattr(server, "_ev", lambda *a, **k: None)
    monkeypatch.setattr(server, "_queue_wake", lambda: None)
    monkeypatch.setattr(ingest, "run_ingest", fake_run)
    server._QUEUE.clear()
    try:
        a = server._enqueue_ingest("https://x/ch/1")
        b = server._enqueue_ingest("https://x/ch/2", source="rerun")
        c = server._enqueue_ingest("https://x/ch/3", source="autopilot")
        for j in (a, b, c):
            server._run_ingest_job(j, server.INGEST[j]["url"])
        assert got == {a: False, b: True, c: False}
    finally:
        server._QUEUE.clear()
        for j in list(got):
            server.INGEST.pop(j, None)
