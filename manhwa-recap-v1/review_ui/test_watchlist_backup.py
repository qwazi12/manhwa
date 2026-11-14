"""A main source that cannot be read has its backup sources asked for the list.

2026-11-14: Asura answered the server with HTTP 403 from 2026-11-05. Downloads
fell back to MGeko; the chapter list did not, so Asura series read "all caught
up" while new chapters were out. No network: every page is a stub.
"""
import os
import sys
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import autopilot as A      # noqa: E402
import providers as P      # noqa: E402
import watchlist as W      # noqa: E402

ASURA = "https://asurascans.com/comics/death-knight-6f7fe6eb"
MGEKO = "https://www.mgeko.cc/manga/death-knight"
DEX = "https://mangadex.org/title/6fa61a0f-95c5-453e-a410-0f458955e0a3"


def _asura(chs):
    return "".join(f'<a href="/comics/death-knight-6f7fe6eb/chapter/{c}">c</a>' for c in chs)


def _mgeko(chs):
    return "".join(f'<a href="/reader/en/death-knight-chapter-{c}-eng-li/">c</a>' for c in chs)


def _forbidden(url):
    raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)


def _setup(asura_chs=range(90, 96)):
    root = tempfile.mkdtemp(prefix="wlb_")
    s = W.add_series(root, "Death Knight")
    W.add_mirror(root, s["id"], ASURA)
    W.add_mirror(root, s["id"], MGEKO)
    W.add_mirror(root, s["id"], DEX)
    W.refresh_series(root, s["id"], _fetcher=lambda u: _asura(asura_chs))
    return root, s["id"]


def _main(root, sid):
    s = W.find(W.load(root), sid)
    return next(m for m in s["mirrors"] if m["source"] == "asura")


def _fetch(mgeko_chs, asura=_forbidden, calls=None):
    def f(url):
        if calls is not None:
            calls.append(url)
        if "asurascans" in url:
            return asura(url)
        if "mgeko" in url:
            return _mgeko(mgeko_chs)
        raise AssertionError("MangaDex must never be asked: " + url)
    return f


def test_main_source_ok_needs_no_backup():
    root, sid = _setup()
    calls = []
    m = W.refresh_series(root, sid, _fetcher=_fetch(range(90, 99), asura=lambda u: _asura(range(90, 97)), calls=calls))
    assert m["status"] == "ok" and m["latest"] == "96" and "backup" not in m
    assert m["last_ok"] and all("mgeko" not in u for u in calls)


def test_blocked_main_source_gets_new_chapters_from_backup():
    root, sid = _setup()
    m = W.refresh_series(root, sid, _fetcher=_fetch(range(1, 97)))
    assert m["status"] == "error" and "403" in m["error"]          # the truth about the main source stays
    assert m["latest"] == "96" and m["chapters"][-2:] == ["95", "96"] and m["chapter_count"] == 7
    assert m["backup"]["added"] == ["96"] and m["backup"]["label"] and m["backup"]["latest"] == "96"
    assert m["from_backup"] == {"96": m["backup"]["series_key"]}
    assert m["first_seen"]["96"] == 0                              # readable on the backup: no early-access wait
    assert A.release_wait_left({"release_wait_hours": {"asura": 24}}, m, "96") == 0
    assert _main(root, sid)["latest"] == "96"                      # saved


def test_backup_is_not_asked_twice_for_the_same_chapter():
    root, sid = _setup()
    W.refresh_series(root, sid, _fetcher=_fetch(range(1, 97)))
    m = W.refresh_series(root, sid, _fetcher=_fetch(range(1, 97)))
    assert m["chapters"].count("96") == 1 and m["backup"]["added"] == []


def test_backup_that_is_behind_adds_nothing():
    root, sid = _setup()
    m = W.refresh_series(root, sid, _fetcher=_fetch(range(1, 94)))
    assert m["latest"] == "95" and m["backup"]["added"] == [] and m["backup"]["skipped"] is None
    assert m["backup"]["latest"] == "93"


def test_backup_with_other_numbering_is_refused():
    root, sid = _setup()
    m = W.refresh_series(root, sid, _fetcher=_fetch(list(range(1, 90)) + [120, 121]))   # no ch.95 there
    assert m["latest"] == "95" and m["backup"]["added"] == [] and "numbering" in m["backup"]["skipped"]
    m = W.refresh_series(root, sid, _fetcher=_fetch(range(1, 140)))                     # 44 "new" chapters
    assert m["latest"] == "95" and "numbering" in m["backup"]["skipped"]


def test_main_source_back_clears_the_backup_note():
    root, sid = _setup()
    W.refresh_series(root, sid, _fetcher=_fetch(range(1, 97)))
    m = W.refresh_series(root, sid, _fetcher=_fetch(range(1, 97), asura=lambda u: _asura(range(90, 98))))
    assert m["status"] == "ok" and m["latest"] == "97" and "backup" not in m
    assert "backup" not in _main(root, sid)


def test_no_backup_leaves_the_error_and_the_old_list():
    root = tempfile.mkdtemp(prefix="wlb_")
    s = W.add_series(root, "Solo")
    W.add_mirror(root, s["id"], ASURA)
    W.add_mirror(root, s["id"], DEX)
    W.refresh_series(root, s["id"], _fetcher=lambda u: _asura(range(1, 6)))
    m = W.refresh_series(root, s["id"], _fetcher=_fetch([]))
    assert m["status"] == "error" and m["latest"] == "5" and "backup" not in m


def _rows(root):
    st = {"settings": dict(A.DEFAULTS), "ledger": {}, "start_from": {}}
    return A.candidates(st, W.view(root)["series"], P.canonical_key)


def test_autopilot_sees_the_backup_chapter_and_says_where_it_came_from():
    root, sid = _setup()
    W.refresh_series(root, sid, _fetcher=_fetch(range(1, 97)))
    row = _rows(root)[0]
    assert row["remaining"][-1] == "96" and row["mirror"]["backup"]["added"] == ["96"]
    assert "main source not readable, chapter list from" in row["reason"]
    assert row["mirror"]["series_url"].startswith("https://asurascans.com")      # made under the same series


def test_unreadable_source_never_reads_as_plainly_caught_up():
    root = tempfile.mkdtemp(prefix="wlb_")
    s = W.add_series(root, "Solo")
    W.add_mirror(root, s["id"], ASURA)
    W.refresh_series(root, s["id"], _fetcher=lambda u: _asura(range(1, 6)))
    st = {"settings": dict(A.DEFAULTS), "ledger": {}, "start_from": {s["id"]: "9"}}
    ok = A.candidates(st, W.view(root)["series"], P.canonical_key)[0]
    assert ok["state"] == "up_to_date" and "not readable" not in ok["reason"]
    W.refresh_series(root, s["id"], _fetcher=_forbidden)
    bad = A.candidates(st, W.view(root)["series"], P.canonical_key)[0]
    assert bad["state"] == "up_to_date" and bad["reason"].endswith("source not readable, this list may be out of date")


def test_refresh_all_route_and_scheduler_refresh_use_the_backup(tmp_path, monkeypatch):
    import ingest
    import server
    root, sid = _setup()
    monkeypatch.setattr(ingest, "PROJECTS", root)
    monkeypatch.setattr(P, "fetch", _fetch(range(1, 97)))
    seen = []
    monkeypatch.setattr(server, "_ev", lambda kind, text, level="info", **k: seen.append((kind, text, level)))
    out = server.api_watchlist_refresh_all()
    assert out["checked"] == 0 and out["from_backup"] == 1
    f = out["failed"][0]
    assert "403" in f["error"] and f["backup_added"] == ["96"] and f["backup_latest"] == "96"
    assert len(seen) == 1 and seen[0][0] == "tracker" and "new ch.96" in seen[0][1] and "403" in seen[0][1]
    monkeypatch.setattr(P, "fetch", _fetch(range(1, 98)))
    server._ap_deps()["refresh"]()                    # the 6-hourly pass
    assert _main(root, sid)["latest"] == "97" and "new ch.97" in seen[-1][1]
    server._ap_deps()["refresh"]()                    # nothing new: no second line
    assert len(seen) == 2
