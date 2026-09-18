"""Watchlist — the planning layer that did not exist.

The tracker was never a watchlist. tracker.build(projects, ...) groups
ALREADY-INGESTED projects, so a title could not appear until money had been
spent on it, and the series identity WAS a site URL — so one story on two
sites was two unrelated rows, and a story on an unrecognised site was no row.

These tests pin the separation that fixes both:

    CANONICAL SERIES   one story, once — exists before anything is ingested
    MIRROR             one place to read it — a series may have several

Everything is stubbed. No network, no real project folders.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


ASURA = "https://asurascans.com/comics/murim-psychopath-6f7fe6eb"
WT = "https://www.webtoons.com/en/action/the-stellar-swordmaster/list?title_no=5988"
WT_VIEWER = ("https://www.webtoons.com/en/action/the-stellar-swordmaster/"
             "episode-7/viewer?title_no=5988&episode_no=7")


def main():
    import providers as P
    import watchlist as W
    root = tempfile.mkdtemp(prefix="wl_")
    # Keys are the PROVIDER'S business, not this test's — Asura keys by
    # normalised URL, WEBTOON by title_no. Deriving them here means the test
    # keeps passing if a provider changes how it identifies a series, and
    # fails if it stops identifying it consistently.
    K_ASURA = P.describe(ASURA)["series_key"]
    K_WT = P.describe(WT)["series_key"]

    # ============================ a title exists BEFORE it is ingested
    s = W.add_series(root, "Murim Psychopath", tier="high_upside", rank=8,
                     aliases=["Dong Bongsu"], keywords=["psychopath murim"])
    check("a title can be added before anything is ingested",
          len(W.load(root)["series"]) == 1)
    check("...and is persisted immediately, not held in memory",
          W.find(W.load(root), s["id"]) is not None)
    check("...with no source attached yet — a legitimate state",
          W.load(root)["series"][0]["mirrors"] == [])
    check("...and it appears in the view with nothing ingested",
          W.view(root, [])["series"][0]["ingested_count"] == 0)
    check("adding the same title twice does not create a second row",
          W.add_series(root, "Murim Psychopath")["id"] == s["id"]
          and len(W.load(root)["series"]) == 1)

    # ============================ one story, several places to read it
    W.add_mirror(root, s["id"], ASURA)
    W.add_mirror(root, s["id"], WT)
    v = W.view(root, [])["series"][0]
    check("a story can carry two sources", v["mirror_count"] == 2)
    check("...and is still ONE row", len(W.view(root, [])["series"]) == 1)
    check("...with the first source preferred by default",
          v["preferred_mirror"] == K_ASURA)
    check("re-attaching the same source is a no-op, not a duplicate",
          W.add_mirror(root, s["id"], WT) and
          len(W.load(root)["series"][0]["mirrors"]) == 2)
    check("...even when the URL is written differently",
          W.add_mirror(root, s["id"], WT_VIEWER) and
          len(W.load(root)["series"][0]["mirrors"]) == 2)

    # THE POINT OF THE CANONICAL LAYER: the same source must never end up on
    # two different stories, because that is the duplicate identity we removed.
    other = W.add_series(root, "Something Else")
    try:
        W.add_mirror(root, other["id"], "https://m.webtoons.com/en/action/x/list?title_no=5988")
        check("attaching one source to two stories is refused", False)
    except W.WatchlistError as e:
        check("attaching one source to two stories is refused", True)
        check("...naming the story that already owns it",
              "Murim Psychopath" in str(e))

    # ============================ preferred source is a real override
    W.set_preferred(root, s["id"], K_WT)
    check("the preferred source can be overridden by hand",
          W.best_mirror(W.find(W.load(root), s["id"]))["source"] == "webtoon")
    try:
        W.set_preferred(root, s["id"], "webtoon:9999")   # not attached
        check("a source that is not attached cannot be preferred", False)
    except W.WatchlistError:
        check("a source that is not attached cannot be preferred", True)

    # ============================ refresh keeps "broken" apart from "empty"
    html = "".join(f"<a href='/comics/x/chapter/{i}'>x</a>" for i in (1, 2, 3))
    m = W.refresh_mirror(root, s["id"], K_ASURA, _fetcher=lambda u: html)
    check("a refresh records what the source has", m["chapter_count"] == 3)
    check("...and its latest chapter", m["latest"] == "3")
    check("...and marks itself checked", m["status"] == "ok" and m["last_checked"])

    def boom(u):
        raise RuntimeError("connection reset")
    bad = W.refresh_mirror(root, s["id"], K_WT, _fetcher=boom)
    check("a FAILED check reports an error", bad["status"] == "error")
    check("...carrying the reason", "connection reset" in (bad["error"] or ""))
    check("...and never renders as 'this source has no chapters'",
          bad["chapter_count"] == 0 and bad["status"] != "ok")

    # ============================ ingest history credits the STORY
    projects = [
        {"id": "murim-psychopath_1", "url": ASURA + "/chapter/1", "chapter": "1"},
        {"id": "murim-psychopath_2", "url": ASURA + "/chapter/2", "chapter": "2"},
        # same story, different site — must count toward the same row
        {"id": "stellar_7", "url": WT_VIEWER, "chapter": "7"},
    ]
    v = next(x for x in W.view(root, projects)["series"] if x["id"] == s["id"])
    check("chapters ingested from EITHER source credit the one story",
          v["ingested_count"] == 3)
    check("...listing what is already made", set(v["ingested"]) == {"1", "2", "7"})
    # Asura published 1-3 and we hold 1, 2 and 7. A COUNT comparison says
    # "3 of 3, up to date" and buries chapter 3; the set says otherwise.
    check("...and flagging that unmade chapters remain", v["has_new"])
    check("...naming exactly which one is missing", v["unmade"] == ["3"])
    check("an unchecked title says so rather than claiming to be up to date",
          not W.view(root, [])["series"][-1]["checked"])
    check("a source with no ingest history reports zero, not an error",
          W.view(root, [])["series"][0]["ingested_count"] == 0)

    # ============================ chapter URLs come from the provider
    ser = W.find(W.load(root), s["id"])
    check("a chapter URL is built by the source that owns it",
          W.chapter_url(ser, K_ASURA, "12") == ASURA + "/chapter/12")
    check("...and WEBTOON builds its own very different shape",
          W.chapter_url(ser, K_WT, "7") == WT_VIEWER)
    try:
        W.chapter_url(ser, "nope:1", "1")
        check("an unattached source cannot build a URL", False)
    except W.WatchlistError:
        check("an unattached source cannot build a URL", True)

    # ============================ removal is not destruction
    W.remove_series(root, other["id"])
    check("a title can be dropped from the watchlist",
          W.find(W.load(root), other["id"]) is None)
    check("...leaving the rest of the list intact",
          W.find(W.load(root), s["id"]) is not None)

    # ============================ the seeded research
    fresh = tempfile.mkdtemp(prefix="wl_seed_")
    res = W.seed(fresh)
    check("the research seeds as 14 canonical titles", res["total"] == 14)
    sv = W.view(fresh, [])["series"]
    check("...every one of them with a source attached",
          all(x["mirror_count"] >= 1 for x in sv))
    check("...every one of them ingestable today",
          all(x["ingestable"] for x in sv))
    check("...covering both WEBTOON and Asura",
          {m["source"] for x in sv for m in x["mirrors"]} == {"webtoon", "asura"})
    check("...ordered greenlight first", sv[0]["tier"] == "greenlight")
    check("...and watchlist last", sv[-1]["tier"] == "watchlist")
    check("re-seeding adds nothing and destroys nothing",
          W.seed(fresh)["added"] == [] and len(W.load(fresh)["series"]) == 14)

    # An edit made by the owner must survive the next seed.
    W.update_series(fresh, "fog-land", tier="greenlight", notes="mine")
    W.seed(fresh)
    kept = W.find(W.load(fresh), "fog-land")
    check("...and an owner's edit survives re-seeding",
          kept["tier"] == "greenlight" and kept["notes"] == "mine")

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
