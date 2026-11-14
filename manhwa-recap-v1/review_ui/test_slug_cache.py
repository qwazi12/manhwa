"""The Library page must not call the source site to name a series it already
resolved (2026-11-13: 13 live requests and 3 s on every load)."""
import research_service as rs


class _P:
    calls = 0

    def chapter_url(self, series_url, ch):
        _P.calls += 1
        if "broken" in series_url:
            raise OSError("source down")
        return series_url.rstrip("/") + "/chapter/" + str(ch)


def _patch(monkeypatch):
    import ingest
    import providers
    _P.calls = 0
    rs._SLUG_CACHE.clear()
    monkeypatch.setattr(providers, "by_name", lambda name: _P())
    monkeypatch.setattr(ingest, "parse_series_chapter",
                        lambda url: (url.split("/comics/")[1].split("/")[0], url.rsplit("/", 1)[1]))


def test_a_mirror_is_resolved_once(monkeypatch):
    _patch(monkeypatch)
    s = {"mirrors": [{"source": "x", "series_url": "https://a/comics/one", "chapters": ["1", "2"]},
                     {"source": "x", "series_url": "https://b/comics/two", "chapters": ["9"]}]}
    assert rs._slugs(s) == ["one", "two"]
    assert rs._slugs(s) == ["one", "two"]
    s["mirrors"][0]["chapters"].append("3")          # a new chapter does not change the series name
    assert rs._slugs(s) == ["one", "two"]
    assert _P.calls == 2


def test_a_failed_lookup_is_tried_again(monkeypatch):
    _patch(monkeypatch)
    s = {"mirrors": [{"source": "x", "series_url": "https://a/comics/broken", "chapters": ["1"]}]}
    assert rs._slugs(s) == []
    assert rs._slugs(s) == []
    assert _P.calls == 2
