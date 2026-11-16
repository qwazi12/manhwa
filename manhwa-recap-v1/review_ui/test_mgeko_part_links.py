"""mgeko lists a chapter's extras (309.1 as 'chapter-309-1') before the chapter.
2026-11-16: Omniscient Reader 309 was made from the 7-page extra 309.1 and 311
from the 2-page 311.5. The chapter's own link must win."""
import io
import urllib.request

import providers

P = providers.MANGAYOMI
SERIES = "https://www.mgeko.cc/manga/manga-1003"
PAGE = "".join(
    f'<a href="/reader/en/manga-1003-chapter-{c}-eng-li/">x</a>'
    for c in ("311-5", "311-2", "311-1", "311", "310", "309-1", "309", "308-5"))


def _url(c):
    return f"https://www.mgeko.cc/reader/en/manga-1003-chapter-{c}-eng-li/"


def test_the_chapter_list_links_the_chapter_not_its_extra():
    got = dict(P.discover_chapters(SERIES, _fetcher=lambda u: PAGE))
    assert got["309"] == _url("309")
    assert got["310"] == _url("310")
    assert got["311"] == _url("311")


def test_an_extra_is_used_only_when_the_chapter_is_not_listed():
    got = dict(P.discover_chapters(SERIES, _fetcher=lambda u: PAGE))
    assert got["308"] == _url("308-5")


def test_the_failover_link_is_the_chapter_not_its_extra(monkeypatch):
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _R(PAGE.encode()))
    assert P.chapter_url(SERIES, "309") == _url("309")
    assert P.chapter_url(SERIES, "311") == _url("311")
    assert P.chapter_url(SERIES, "308") == _url("308-5")


def test_a_named_chapter_link_is_still_the_chapter():
    # Damn Reincarnation on mgeko: 81.5 is listed before "81 (season 2 end)"
    page = "".join(f'<a href="/reader/en/damn-reincarnation-chapter-{c}-eng-li/">x</a>'
                   for c in ("82", "81-5", "81-s2-end", "80"))
    got = dict(P.discover_chapters("https://www.mgeko.cc/manga/damn-reincarnation", _fetcher=lambda u: page))
    assert got["81"].endswith("damn-reincarnation-chapter-81-s2-end-eng-li/")
