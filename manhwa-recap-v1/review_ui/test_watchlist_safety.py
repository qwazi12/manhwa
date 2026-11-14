"""The watchlist file can no longer be damaged, emptied or half-updated.

2026-11-14 incident (production): the 6-hourly source pass and a manual
"refresh all" saved at the same moment through one shared ".tmp" name. The
file that landed could not be parsed, load() answered "no series" for it, and
the studio showed an empty watchlist. There was no backup of this file.
"""
import glob
import json
import os
import sys
import tempfile
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import providers as P      # noqa: E402
import watchlist as W      # noqa: E402

ASURA = "https://asurascans.com/comics/death-knight-6f7fe6eb"
MGEKO = "https://www.mgeko.cc/manga/death-knight"


def _page(chs):
    return "".join(f'<a href="/comics/death-knight-6f7fe6eb/chapter/{c}">c</a>' for c in chs)


def _root(n=3):
    root = tempfile.mkdtemp(prefix="wls_")
    for i in range(n):
        s = W.add_series(root, f"Series {i}")
        W.add_mirror(root, s["id"], f"https://asurascans.com/comics/series-{i}-6f7fe6eb")
    return root


def test_many_writers_at_once_never_damage_the_file():
    root = _root(40)
    errors = []

    def work(i):
        try:
            for n in range(25):
                W.update_series(root, f"series-{i}", notes="x" * (200 + n))
                assert len(W.load(root)["series"]) == 40
        except Exception as e:  # noqa
            errors.append(repr(e))
    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    data = json.load(open(W.store_path(root)))                 # parses: not half of two saves
    assert len(data["series"]) == 40
    assert all(s["notes"] == "x" * 224 for s in data["series"][:8])      # no writer's change was lost
    assert glob.glob(os.path.join(root, "*.tmp")) == []


def test_damaged_file_reads_from_the_last_good_copy():
    root = _root(3)
    W.update_series(root, "series-0", notes="kept")            # a second save: the first is now the good copy
    open(W.store_path(root), "w").write('{"series": [{"id": "series-0", "ti')
    data = W.load(root)
    assert len(data["series"]) == 3 and data["recovered_from_backup"]
    assert "last good copy" in W.view(root)["warning"]
    W.update_series(root, "series-1", notes="after")           # saving heals the main file
    assert len(json.load(open(W.store_path(root)))["series"]) == 3
    assert "recovered_from_backup" not in json.load(open(W.store_path(root)))


def test_damaged_file_with_no_good_copy_is_never_an_empty_list_that_can_be_saved():
    root = tempfile.mkdtemp(prefix="wls_")
    open(W.store_path(root), "w").write('{"series": [{"id": "a", "ti')
    assert W.load(root) == {"series": [], "damaged": True}
    v = W.view(root)
    assert v["series"] == [] and "damaged" in v["error"]
    with pytest.raises(W.WatchlistError):
        W.add_series(root, "New")                              # would have saved a 1-series list over 23
    assert open(W.store_path(root)).read() == '{"series": [{"id": "a", "ti'      # untouched


def test_a_list_with_series_is_not_replaced_by_an_empty_one():
    root = _root(2)
    with pytest.raises(W.WatchlistError):
        W.save(root, {"series": []})
    assert len(W.load(root)["series"]) == 2
    assert W.remove_series(root, "series-0") and W.remove_series(root, "series-1")   # the owner removing the last one is allowed
    assert W.load(root)["series"] == []
    assert not W.remove_series(root, "nope")


def test_restore_from_an_api_copy():
    root = _root(3)
    W.refresh_mirror(root, "series-0", W.load(root)["series"][0]["mirrors"][0]["series_key"],
                     _fetcher=lambda u: _page(range(1, 6)).replace("death-knight", "series-0"))
    copy = json.loads(json.dumps(W.view(root, [])["series"]))            # what GET /api/watchlist returned
    saved = json.loads(json.dumps(W.load(root)["series"]))
    open(W.store_path(root), "w").write('{"series": [{"id"')            # the incident
    os.remove(W.store_path(root) + ".bak")
    assert W.load(root).get("damaged")
    assert W.restore(root, copy) == (3, 0)
    now = W.load(root)["series"]
    # display-only fields dropped; every source keeps its chapters, dates and first-seen times.
    # The one thing the API copy does not carry is when the series was added (read by nothing).
    for a, b in zip(sorted(now, key=lambda s: s["id"]), sorted(saved, key=lambda s: s["id"])):
        assert set(b) - set(a) == {"added_at"} and not set(a) - set(b)
        assert {k: v for k, v in b.items() if k != "added_at"} == a
    assert glob.glob(W.store_path(root) + ".before-restore-*")           # the damaged file is kept
    with pytest.raises(W.WatchlistError):
        W.restore(root, copy[:1])                                        # has series: needs replace
    assert W.restore(root, copy[:1], replace=True) == (1, 3)


@pytest.mark.parametrize("bad", [[], "x", [{"id": "a"}], [{"id": "a", "title": "A", "mirrors": [{"source": "asura"}]}],
                                 [{"id": "a", "title": "A", "mirrors": []}, {"id": "a", "title": "A", "mirrors": []}]])
def test_restore_refuses_a_bad_copy(bad):
    root = tempfile.mkdtemp(prefix="wls_")
    with pytest.raises(W.WatchlistError):
        W.restore(root, bad)
    assert not os.path.exists(W.store_path(root))


def test_a_refresh_does_not_undo_a_change_made_while_the_page_was_loading():
    root = tempfile.mkdtemp(prefix="wls_")
    s = W.add_series(root, "Death Knight")
    W.add_mirror(root, s["id"], ASURA)
    key = W.load(root)["series"][0]["mirrors"][0]["series_key"]

    def slow_page(url):
        W.add_mirror(root, s["id"], MGEKO)                     # the owner attaches a source meanwhile
        W.add_series(root, "Another Series")
        return _page(range(1, 4))
    m = W.refresh_mirror(root, s["id"], key, _fetcher=slow_page)
    data = W.load(root)
    assert m["latest"] == "3" and [x["id"] for x in data["series"]] == ["death-knight", "another-series"]
    assert len(data["series"][0]["mirrors"]) == 2 and data["series"][0]["mirrors"][0]["latest"] == "3"


def test_restore_route(monkeypatch):
    import ingest
    import server
    from fastapi import HTTPException
    root = _root(2)
    copy = W.view(root, [])["series"]
    open(W.store_path(root), "w").write("{")
    os.remove(W.store_path(root) + ".bak")
    monkeypatch.setattr(ingest, "PROJECTS", root)
    seen = []
    monkeypatch.setattr(server, "_ev", lambda kind, text, level="info", **k: seen.append((kind, text, level)))
    assert "damaged" in server.api_watchlist()["error"]
    assert server.api_watchlist_restore(server.WLRestoreIn(series=copy)) == {"ok": True, "series": 2, "before": 0}
    assert len(server.api_watchlist()["series"]) == 2 and "error" not in server.api_watchlist()
    assert seen and "restored" in seen[0][1] and seen[0][2] == "warn"
    with pytest.raises(HTTPException) as e:
        server.api_watchlist_restore(server.WLRestoreIn(series=copy))
    assert e.value.status_code == 409
