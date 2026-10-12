"""Autopilot waits out the early-access paywall after a chapter drops (2026-10-12)."""
import autopilot as ap
import providers
import watchlist as wl


def test_wait_per_source_and_old_chapters_never_wait():
    now = 1_000_000.0
    asura = {"source": "asura", "first_seen": {"46": now - 3600, "45": 0}}
    other = {"source": "webtoon", "first_seen": {"10": now - 3 * 3600}}
    s = {"release_wait_hours": {"asura": 24, "default": 2}}
    assert 22 * 3600 < ap.release_wait_left(s, asura, "46", now) <= 23 * 3600
    assert ap.release_wait_left(s, asura, "45", now) == 0                 # known before the rule
    assert ap.release_wait_left(s, other, "10", now) == 0                 # 2 h default passed
    assert ap.release_wait_left({}, asura, "46", now) > 0                 # defaults apply
    assert "free release" in ap._wait_reason("46", 3600, now)


def test_settings_validate_the_wait():
    import tempfile
    root = tempfile.mkdtemp()
    _b, after = ap.update_settings(root, {"release_wait_hours": {"asura": 12}})
    assert after["release_wait_hours"]["asura"] == 12 and after["release_wait_hours"]["default"] == 2
    try:
        ap.update_settings(root, {"release_wait_hours": {"asura": 500}})
        assert False
    except ValueError:
        pass


def test_refresh_records_first_seen(tmp_path, monkeypatch):
    root = str(tmp_path)
    data = {"series": [{"id": "m", "title": "M", "mirrors": [{"source": "asura", "series_key": "k",
                                                              "series_url": "https://x/m", "chapters": []}]}]}
    wl.save(root, data)

    class P:
        def discover_chapters(self, url, _fetcher=None):
            return P.found
    P.found = [("44", "u44"), ("45", "u45")]
    monkeypatch.setattr(providers, "by_name", lambda n: P())
    monkeypatch.setattr(providers, "release_info", lambda *a: {"dates": {}, "cover": None})
    m = wl.refresh_mirror(root, "m", "k", _fetcher=lambda u: "")
    assert m["first_seen"] == {"44": 0, "45": 0}                          # first check: all count as old
    P.found = P.found + [("46", "u46")]
    m = wl.refresh_mirror(root, "m", "k", _fetcher=lambda u: "")
    assert m["first_seen"]["44"] == 0 and m["first_seen"]["46"] > 0       # the new one is timed
