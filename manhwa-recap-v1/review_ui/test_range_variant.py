"""A saved version beside a chapter ("111 (recut)") counts as that chapter when
picked for a merge. Live 2026-11-21: Dark Mage [111-recut, 112] was built as
"112-112" because the version's chapter field didn't parse and it was dropped."""
import json
import os

import pytest
from fastapi import HTTPException

import ingest
import server


def _chapter(root, pid, chapter, variant=""):
    d = root / pid
    (d / "exports").mkdir(parents=True)
    (d / "exports" / "final.mp4").write_bytes(b"v")
    json.dump({"id": pid, "series": "Dark Mage", "series_id": "dm", "chapter": chapter, "variant": variant,
               "url": "https://asurascans.com/comics/dm/chapter/" + chapter.split()[0]},
              open(d / "project.json", "w"))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "PROJECTS", str(tmp_path))
    monkeypatch.setattr(server, "_series_pack", lambda *a, **k: {})
    return tmp_path


def test_variant_counts_as_its_chapter(lib):
    _chapter(lib, "dm_111-recut", "111 (recut)", "recut")
    _chapter(lib, "dm_112", "112")
    body = server.RangeIn(projects=["dm_111-recut", "dm_112"])
    _p, sid, _s, _pk, pl, a, b = server._range_ctx(body)
    assert (a, b) == (111, 112)
    assert [n for n, _c, _x in pl["chapters"]] == [111, 112]


def test_a_picked_project_without_a_chapter_is_refused_not_dropped(lib):
    _chapter(lib, "dm_112", "112")
    _chapter(lib, "dm_extra", "bonus", "")
    with pytest.raises(HTTPException) as e:
        server._range_ctx(server.RangeIn(projects=["dm_extra", "dm_112"]))
    assert e.value.status_code == 400
