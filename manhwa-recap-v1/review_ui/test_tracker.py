"""Chapter-tracker tests (Session 27).

The rule this file exists to protect: a series whose check FAILED must never
render as up to date. That is the same silence that let a 3-of-11-page scrape
reach a finished video.

Run: python3 test_tracker.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tracker

SLUG = "https://asurascans.com/comics/some-series-ab12"


def _page(nums):
    return "".join(f'<a href="/comics/some-series-ab12/chapter/{n}">ch {n}</a>' for n in nums)


def _projects(chapters):
    return [{"id": f"some-series_{c}", "url": f"{SLUG}/chapter/{c}",
             "series": "Some Series", "chapter": str(c)} for c in chapters]


def main():
    r = []
    root = tempfile.mkdtemp(prefix="trk_")

    # ---- url derivation
    r.append(("a chapter url yields its series page",
              tracker.series_page_url(f"{SLUG}/chapter/7") == SLUG + "/"))
    r.append(("a trailing slash is handled",
              tracker.series_page_url(f"{SLUG}/chapter/7/") == SLUG + "/"))
    r.append(("a non-chapter url is not trackable",
              tracker.series_page_url("https://site/comics/x") is None
              and tracker.series_page_url("") is None))

    # ---- chapter list parsing
    nums = tracker.chapter_numbers(SLUG + "/", _fetcher=lambda u: _page([1, 2, 10, 3]))
    r.append(("chapter numbers are parsed and sorted", nums == [1.0, 2.0, 3.0, 10.0]))
    try:
        tracker.chapter_numbers(SLUG + "/", _fetcher=lambda u: "<html>no links</html>")
        raised = False
    except ValueError:
        raised = True
    r.append(("a page with no chapter links RAISES rather than returning empty", raised))

    # ---- the gap report
    d = tracker.build(_projects([1, 2]), root,
                      _fetcher=lambda u: _page(range(1, 6)), now=1000)
    sx = d["series"][0]
    r.append(("counts how many chapters are NEWER than what we hold", sx["behind"] == 3))
    r.append(("names the NEXT chapter, not an arbitrary one", sx["next"] == "3"))
    r.append(("builds a usable ingest url",
              sx["next_url"] == SLUG + "/chapter/3"))
    r.append(("reports what is already held", sx["highest_have"] == "2" and sx["have_count"] == 2))
    r.append(("reports the latest published", sx["latest"] == "5"))
    r.append(("no error on a clean check", sx["error"] is None))

    # ---- up to date
    d = tracker.build(_projects([1, 2, 3]), root,
                      _fetcher=lambda u: _page([1, 2, 3]), now=2000, refresh=True)
    sx = d["series"][0]
    r.append(("a fully-ingested series reports zero behind and no next",
              sx["behind"] == 0 and sx["next"] is None))

    # ---- holding a LATE chapter must not make the back catalogue look new
    rootL = tempfile.mkdtemp(prefix="trkL_")
    d = tracker.build(_projects([338]), rootL,
                      _fetcher=lambda u: _page(range(0, 339)), now=2500)
    sx = d["series"][0]
    r.append(("holding the latest chapter reports zero new, not the whole backlog",
              sx["behind"] == 0 and sx["next"] is None))
    r.append(("...and the skipped earlier chapters show as BACKFILL instead",
              sx["backfill_count"] == 338))
    d = tracker.build(_projects([338]), tempfile.mkdtemp(prefix="trkL2_"),
                      _fetcher=lambda u: _page(range(0, 341)), now=2600)
    sx = d["series"][0]
    r.append(("a genuinely newer chapter is offered next",
              sx["behind"] == 2 and sx["next"] == "339"))

    # ---- THE important one: a failed check is not "up to date"
    root2 = tempfile.mkdtemp(prefix="trk2_")
    def boom(u): raise ConnectionError("source unreachable")
    d = tracker.build(_projects([1]), root2, _fetcher=boom, now=3000)
    sx = d["series"][0]
    r.append(("a failed check reports an ERROR", bool(sx["error"])
              and "source unreachable" in sx["error"]))
    r.append(("...and does NOT claim the series is up to date",
              sx["next"] is None and sx["latest"] is None and d["errors"] == 1))

    # ---- a failed re-check falls back to the cache, flagged as stale
    root3 = tempfile.mkdtemp(prefix="trk3_")
    tracker.build(_projects([1]), root3, _fetcher=lambda u: _page([1, 2, 3]), now=4000)
    d = tracker.build(_projects([1]), root3, _fetcher=boom, now=4000 + tracker.TTL_SECONDS + 1)
    sx = d["series"][0]
    r.append(("a failed re-check still shows the last known list, marked stale",
              sx["stale"] is True and sx["latest"] == "3" and bool(sx["error"])))

    # ---- caching avoids re-fetching inside the TTL
    root4 = tempfile.mkdtemp(prefix="trk4_")
    hits = []
    def counting(u):
        hits.append(u); return _page([1, 2])
    tracker.build(_projects([1]), root4, _fetcher=counting, now=5000)
    tracker.build(_projects([1]), root4, _fetcher=counting, now=5000 + 60)
    r.append(("a second look inside the TTL does not re-fetch", len(hits) == 1))
    tracker.build(_projects([1]), root4, _fetcher=counting, now=5000 + 60, refresh=True)
    r.append(("refresh=True forces a re-fetch", len(hits) == 2))

    # ---- a long backlog is capped
    root5 = tempfile.mkdtemp(prefix="trk5_")
    d = tracker.build(_projects([1]), root5,
                      _fetcher=lambda u: _page(range(1, 300)), now=6000)
    sx = d["series"][0]
    r.append(("a 200+ chapter backlog is reported in full as a COUNT",
              sx["behind"] == 298))
    r.append(("...but the UI list is capped at 25", len(sx["upcoming"]) == 25))

    # ---- untrackable projects are skipped, not crashed on
    d = tracker.build([{"id": "legacy", "url": "loaded", "series": "L", "chapter": "1"}],
                      tempfile.mkdtemp(prefix="trk6_"), _fetcher=lambda u: _page([1]))
    r.append(("a project with no usable url is skipped", d["series"] == []))

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
