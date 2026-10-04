"""Paste-a-link tracking (owner, 2026-10-04): pasting an Asura/WEBTOON series
or chapter link adds the series with its real title, attaches the source,
checks chapters and researches the cast in the background, never duplicates
(Asura's rotating link code included) and can queue the pasted chapter.
Also: a cast whose citations came back empty is rebuilt from Google's grounding
instead of being rejected. Network and model calls faked."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []
SERIES = "https://asurascans.com/comics/a-regressors-tale-of-cultivation-3ec3b16f/"
CHAPTER = SERIES + "chapter/30"


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import providers
    import series_research as sr
    # ---- the research fix
    src = [{"tier": "official"}, {"tier": "wiki"}, {"tier": "low"}]
    rec = {"sources": src, "claims": [
        {"text": "The protagonist, Seo Eunhyun, possesses zero aptitude", "sources": [0, 1]},
        {"text": "Kim Young-hoon is a coworker with a rare physique", "sources": [1]},
        {"text": "Rumour Bob was seen on a forum", "sources": [2]},
        {"text": "Seven modern South Korean colleagues are transmigrated into a ruthless world of immortal cultivators", "sources": [0]}],
           "structured": {"characters": [{"name": "Seo Eunhyun", "role": "protagonist", "sources": []},
                                         {"name": "Kim Young-hoon", "sources": ["1"]},
                                         {"name": "Rumour Bob", "sources": []}],
                          "world_setting": {"premise": "Seven South Korean colleagues transmigrated into a world of immortal cultivators", "sources": []}}}
    b = sr.to_bible("A Regressor's Tale", [], rec)
    names = [c["name"] for c in b["characters"]]
    check("a character with EMPTY citations is kept when grounded sentences name them", "Seo Eunhyun" in names)
    check("citations written as text (\"1\") are accepted", "Kim Young-hoon" in names)
    check("a character only low sources mention is still rejected", b["research"]["rejected_characters"] == ["Rumour Bob"])
    check("the given name alone finds them (\"Eunhyun\")", sr._grounded(sr._name_keys({"name": "Seo Eunhyun"}),
                                                                     [{"text": "Eunhyun dies again", "sources": [0]}]) == [0])
    check("world facts are verified from overlapping grounded sentences", b["world_setting"].get("premise"))

    calls = []
    sr_restructure, sr_research = sr.restructure, sr.research
    sr.restructure = lambda title, saved, key, _urlopen=None: calls.append("restructure") or dict(rec, at=1)
    sr.research = lambda *a, **k: calls.append("search") or dict(rec, at=2)

    # ---- the tracker
    import ingest
    import research_service
    import server
    import series_bible
    import watchlist
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="qa_")
    saved = (ingest.PROJECTS, series_bible.BIBLES_DIR, providers.fetch, server._quick_followup, server._enqueue_ingest)
    ingest.PROJECTS = root
    series_bible.BIBLES_DIR = os.path.join(root, "_series_bibles")
    watchlist.save(root, {"series": []})
    fetched, follow, queued = [], [], []
    providers.fetch = lambda url, timeout=None: fetched.append(url) or (
        '<meta property="og:title" content="Fog Land | WEBTOON">' if "webtoons" in url else
        '<meta property="og:title" content="A Regressor&#x27;s Tale of Cultivation | Asura Scans">')
    server._quick_followup = lambda sid, key: follow.append((sid, key))
    server._enqueue_ingest = lambda url, **k: queued.append(url) or "job9"
    try:
        c = TestClient(server.app)
        r = c.post("/api/watchlist/quick", json={"url": SERIES, "tier": "greenlight"}).json()
        check("a pasted series link adds the series with the page's real title",
              r["created"] and r["title"] == "A Regressor's Tale of Cultivation" and fetched == [SERIES])
        wl = watchlist.load(root)["series"]
        check("...with its source attached", len(wl) == 1 and wl[0]["mirrors"][0]["source"] == "asura"
              and wl[0]["tier"] == "greenlight")
        check("...and its chapters and cast are fetched in the background", follow and follow[-1][0] == wl[0]["id"])
        check("a series link doesn't queue anything", r["chapter"] is None and not queued)
        rotated = SERIES.replace("3ec3b16f", "6f7fe6eb") + "chapter/30"
        r = c.post("/api/watchlist/quick", json={"url": rotated, "make_chapter": True}).json()
        check("the same series with Asura's rotated code is NOT duplicated",
              not r["created"] and len(watchlist.load(root)["series"]) == 1)
        check("a chapter link can queue that chapter", r["chapter"] == "30" and r["job"] == "job9" and queued == [rotated])
        os.makedirs(os.path.join(root, ingest.project_id(CHAPTER)))
        open(os.path.join(root, ingest.project_id(CHAPTER), "segments.json"), "w").write("[]")
        r = c.post("/api/watchlist/quick", json={"url": CHAPTER, "make_chapter": True}).json()
        check("a chapter that's already made isn't made again", r["job"] is None and "already made" in r["note"])
        check("non-links are refused", c.post("/api/watchlist/quick", json={"url": "regressor"}).status_code == 400)
        check("unsupported sites are refused with a reason",
              c.post("/api/watchlist/quick", json={"url": "https://example.com/manga/x"}).status_code == 400)
        r = c.post("/api/watchlist/quick", json={"url": "https://www.webtoons.com/en/fantasy/fog-land/list?title_no=9299"}).json()
        check("a WEBTOON link works the same way", r["created"] and r["title"] == "Fog Land"
              and len(watchlist.load(root)["series"]) == 2)

        # never-checked sources get checked by the scheduler
        checked = []
        real = watchlist.refresh_mirror
        watchlist.refresh_mirror = lambda root_, sid, key, _fetcher=None: checked.append(sid) or \
            {"status": "ok", "chapter_count": 30, "latest": "30"}
        try:
            n = server._check_new_sources()
        finally:
            watchlist.refresh_mirror = real
        check("the scheduler checks every never-checked source", n == 2 and len(checked) == 2)

        # research: a bible with no cast is rebuilt from the saved research, no new search
        sid = watchlist.load(root)["series"][0]["id"]
        s = research_service._series(sid)
        for slug in research_service._slugs(s):
            series_bible.save_series_bible(slug, {"canonical_title": s["title"], "characters": [],
                                                  "research": {"text": "saved", "claims": rec["claims"], "sources": src}})
        check("a bible with no cast counts as needing research", research_service.needs_research(s))
        out = research_service.build(sid, api_key="AQ.x")
        check("...and is rebuilt from the saved research without a new search",
              calls == ["restructure"] and [c["name"] for c in out["characters"]] == ["Seo Eunhyun", "Kim Young-hoon"])
    finally:
        (ingest.PROJECTS, series_bible.BIBLES_DIR, providers.fetch, server._quick_followup,
         server._enqueue_ingest) = saved
        sr.restructure, sr.research = sr_restructure, sr_research

    ing = open(os.path.join(HERE, "ingest.py"), encoding="utf-8").read()
    check("ingest re-researches a series whose bible has no cast",
          'if bible is None or not bible.get("characters"):' in ing)
    check("ingest replaces a chapter's empty-cast copy once the series has a cast", "_local_cast" in ing)
    import pipeline_steps
    check("re-describing a chapter refreshes its copy of the cast list", "series_bible.json" in pipeline_steps.CLEARS["describe"])
    sb = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    check("the Tracker has the paste bar", 'id="qurl"' in sb and "qaddGo()" in sb)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
