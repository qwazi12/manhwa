"""Providers — the source layer that used to be hardcoded to one site.

Before this, "which site is this" was not a question the code asked. The URL
regexes in ingest.py WERE Asura's URL shape, so a WEBTOON episode URL did not
fail — it parsed into nonsense and carried on:

    parse_series_chapter(webtoon_episode_url)
      -> ('episode-1', 'viewer?title_no=5988&episode_no=1')

No error, no warning, a project folder named after a query string. These tests
pin the three behaviours that stops:

1. Identity comes from the PROVIDER, not the URL text, so the same series
   reached four different ways is one series.
2. Every source declares what it can actually do, so the UI can say "partial"
   instead of half-working.
3. Extraction is source-specific, because the generic heuristic is actively
   wrong on WEBTOON (see the dominant-directory note below).

Nothing here touches the network: every discovery call takes a _fetcher stub.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


ASURA_SERIES = "https://asurascans.com/comics/murim-psychopath-6f7fe6eb"
WT_LIST = ("https://www.webtoons.com/en/action/the-stellar-swordmaster/"
           "list?title_no=5988")
WT_VIEWER = ("https://www.webtoons.com/en/action/the-stellar-swordmaster/"
             "episode-7/viewer?title_no=5988&episode_no=7")
WT_MOBILE = "https://m.webtoons.com/en/action/the-stellar-swordmaster/list?title_no=5988"


def main():
    import providers as P

    # ===================================== routing
    check("an Asura URL routes to the Asura provider",
          P.for_url(ASURA_SERIES).name == "asura")
    check("a WEBTOON list URL routes to the WEBTOON provider",
          P.for_url(WT_LIST).name == "webtoon")
    check("a WEBTOON viewer URL routes there too",
          P.for_url(WT_VIEWER).name == "webtoon")
    check("an unknown site still routes somewhere, never crashes",
          P.for_url("https://example.com/manga/foo/ch/3").name == "generic")
    check("WEBTOON is matched before the generic fallback",
          P.REGISTRY.index(P.WEBTOON) < P.REGISTRY.index(P.GENERIC))

    # ===================================== identity
    key = P.describe(WT_LIST)["series_key"]
    check("a WEBTOON series is keyed by title_no, not by its URL text",
          key == "webtoon:5988")
    check("...so the viewer URL is the SAME series", P.describe(WT_VIEWER)["series_key"] == key)
    check("...and the mobile URL is the same series too",
          P.describe(WT_MOBILE)["series_key"] == key)
    check("...and the normalised URL is the canonical desktop list page",
          P.describe(WT_VIEWER)["series_url"] == WT_LIST)
    other = P.describe("https://www.webtoons.com/en/fantasy/x/list?title_no=6465")
    check("a different title_no is a different series", other["series_key"] != key)
    check("two different Asura series stay distinct",
          P.describe(ASURA_SERIES)["series_key"] !=
          P.describe("https://asurascans.com/comics/other-thing-1a2b")["series_key"])

    # ===================================== declared support
    check("every provider states a support level",
          all(p.support in (P.SUPPORTED, P.PARTIAL, P.FALLBACK, P.UNSUPPORTED)
              for p in P.REGISTRY))
    check("the generic fallback does NOT claim full support",
          P.GENERIC.support != P.SUPPORTED)
    check("...and describe() surfaces that to the caller",
          P.describe("https://example.com/x")["support"] != P.SUPPORTED)

    # ===================================== discovery (stubbed)
    asura_html = ("<a href='/comics/murim-psychopath-6f7fe6eb/chapter/1'>1</a>"
                  "<a href='/comics/murim-psychopath-6f7fe6eb/chapter/12'>12</a>"
                  "<a href='/comics/murim-psychopath-6f7fe6eb/chapter/3'>3</a>"
                  "<a href='/comics/murim-psychopath-6f7fe6eb/chapter/12'>dup</a>")
    ch = P.ASURA.discover_chapters(ASURA_SERIES, _fetcher=lambda u: asura_html)
    check("Asura discovery finds every chapter link", len(ch) == 3)
    check("...deduplicated", [c for c, _ in ch] == ["1", "3", "12"])
    check("...in numeric order, not string order",
          [c for c, _ in ch].index("12") == 2)
    check("...and builds a working chapter URL",
          ch[-1][1] == ASURA_SERIES + "/chapter/12")

    wt_html = "".join(f"<a href='/en/action/x/episode-{i}/viewer?title_no=5988&episode_no={i}'>x</a>"
                      for i in (1, 2, 3, 128, 129))
    wch = P.WEBTOON.discover_chapters(WT_LIST, _fetcher=lambda u: wt_html)
    check("WEBTOON discovery infers the full run from the highest episode",
          len(wch) == 129 and wch[0][0] == "1" and wch[-1][0] == "129")
    check("...because the list page only ever shows a paginated handful",
          "129" in [c for c, _ in wch] and "64" in [c for c, _ in wch])
    check("...and its chapter URL is a real viewer URL",
          wch[6][1] == WT_VIEWER)

    # A source that answers with nothing is an ERROR, never an empty list:
    # the tracker's original bug was a failed check rendering as "up to date".
    for label, prov, html in [("Asura", P.ASURA, "<html>nothing</html>"),
                              ("WEBTOON", P.WEBTOON, "<html>nothing</html>")]:
        try:
            prov.discover_chapters(WT_LIST, _fetcher=lambda u: html)
            check(f"{label} raises when it finds no chapters", False)
        except ValueError:
            check(f"{label} raises when it finds no chapters", True)

    # ===================================== extraction
    # THE BUG THIS PINS: the generic extractor keeps the images in whichever
    # DIRECTORY appears most. On WEBTOON that rule inverts — each strip slice
    # sits in its own dated CDN folder while ~500 UI assets share one — so a
    # 1027-image page reduced to a single page of furniture.
    slices = "".join(
        f'<img class="_images" data-url="https://webtoon-phinf.pstatic.net/'
        f'2024010{i%9}_{i}/strip_{i}.jpg" src="data:image/gif;base64,x">'
        for i in range(1, 61))
    furniture = "".join(
        f'<img src="https://webtoons-static.pstatic.net/ui/thumb_{i}.png">'
        for i in range(1, 121))
    pages = P.WEBTOON.extract_pages("<html>" + furniture + slices + furniture + "</html>")
    check("the WEBTOON extractor returns the strip, not the furniture",
          len(pages) == 60)
    check("...every page from the strip CDN",
          all("webtoon-phinf" in u for u in pages))
    check("...and none of the static UI host",
          not any("webtoons-static" in u for u in pages))
    check("...in page order", pages[0].endswith("strip_1.jpg"))
    check("...and the result passes its own quality check",
          P.WEBTOON.quality_check(pages)[0])
    check("a single-image result FAILS the quality check",
          not P.WEBTOON.quality_check(pages[:1])[0])

    # ===================================== ingest naming
    import ingest
    check("Asura project naming is unchanged",
          ingest.parse_series_chapter(ASURA_SERIES + "/chapter/12")
          == ("murim-psychopath-6f7fe6eb", "12"))
    check("a WEBTOON episode no longer becomes a query-string folder name",
          ingest.parse_series_chapter(WT_VIEWER) ==
          ("the-stellar-swordmaster-5988", "7"))
    slug = ingest._slug(WT_VIEWER)
    check("...and its project id is readable", slug == "the-stellar-swordmaster-5988_7")
    check("...with no query punctuation left in it",
          not any(c in slug for c in "?&="))

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
