"""Series board (owner, 2026-10-04): Release calendar + Watchlist merged, one
card per series. "Not made yet" was every chapter since ch.1 and ignored the
autopilot ledger; the board shows made / next / left in the plan / earlier not
planned / new since last made, chapter lists load per card, and a back
catalogue is planned only by the owner's "plan backfill" button."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    import ingest
    import server
    import watchlist
    import autopilot
    from fastapi.testclient import TestClient
    root = tempfile.mkdtemp(prefix="sb_")
    saved = ingest.PROJECTS
    ingest.PROJECTS = root
    watchlist.save(root, {"series": [{"id": "stellar", "title": "The Stellar Swordmaster", "aliases": [],
                                      "tier": "greenlight", "rank": 1,
                                      "mirrors": [{"series_key": "webtoon:5988", "source": "webtoon", "label": "WEBTOON",
                                                   "series_url": "https://www.webtoons.com/en/action/s/list?title_no=5988",
                                                   "support": "supported", "status": "ok",
                                                   "chapters": [str(i) for i in range(1, 132)], "latest": "131",
                                                   "release_dates": {"131": ["2026-09-30", False]}}]}]})
    st = autopilot.load(root)
    st["ledger"]["webtoon:5988|129"] = {"series_id": "stellar", "chapter": "129", "status": "done",
                                         "source": "autopilot", "project": "p129", "queued_at": 1}
    st["ledger"]["webtoon:5988|5"] = {"series_id": "stellar", "chapter": "5", "status": "done",
                                       "source": "manual", "project": "deleted-later", "queued_at": 1}
    autopilot.save(root, st)
    try:
        c = TestClient(server.app)
        b = c.get("/api/series/board").json()["series"][0]
        check("tier shows its new name", b["tier_label"] == "Make now")
        check("made counts the ledger (a deleted project's chapter stays made)", "129" in b["made"] and "5" in b["made"])
        check("next is the next chapter in the plan, not ch.1", b["next"] == "130" and b["plan_from"] == "129")
        check("left in the plan = 130, 131 (was '130 not made yet')", b["left_in_plan"] == ["130", "131"])
        check("the back catalogue is counted separately", b["earlier_not_planned"] == 127)
        check("new since the last one made", b["new_since_made"] == ["130", "131"])
        check("latest release date comes with the card", b["latest_date"] == "2026-09-30")
        ch = c.get("/api/series/chapters", params={"series_id": "stellar"}).json()["chapters"]
        check("chapters load per card, newest first, with made flags",
              ch[0]["ch"] == "131" and next(x for x in ch if x["ch"] == "129")["made"])
        check("unknown series -> 404", c.get("/api/series/chapters", params={"series_id": "x"}).status_code == 404)
        r = c.post("/api/autopilot/backfill", json={"series_id": "stellar", "from_chapter": "120"})
        check("plan backfill from ch.120", r.status_code == 200 and r.json()["plan_from"] == "120")
        b = c.get("/api/series/board").json()["series"][0]
        check("...autopilot's next is now ch.120 (story order)", b["next"] == "120" and b["earlier_not_planned"] == 118)
        check("backfill refuses an unlisted chapter",
              c.post("/api/autopilot/backfill", json={"series_id": "stellar", "from_chapter": "999"}).status_code == 400)
    finally:
        ingest.PROJECTS = saved
    src = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    check("board has the owner's filters and the per-card backfill button",
          "sbFilter('new'" in src and "plan backfill from here" in src and "function sbChapters(" in src)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
