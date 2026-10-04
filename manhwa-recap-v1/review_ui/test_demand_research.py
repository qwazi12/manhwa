"""Demand research (owner, 2026-10-04, step 6): weekly YouTube check of how
recaps of each series do; suggestions only. Fake YouTube client; no network."""
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []
NOW = datetime(2026, 10, 4, tzinfo=timezone.utc).timestamp()


def check(name, ok):
    R.append((name, bool(ok)))


def iso(days_ago):
    return datetime.fromtimestamp(NOW - days_ago * 86400, timezone.utc).isoformat().replace("+00:00", "Z")


class FakeYT:
    def __init__(self, results, stats):
        self.results, self.stats, self.spent, self.queries = results, stats, 0, []

    def search_recaps(self, q, limit=8):
        self.spent += 100
        self.queries.append(q)
        for k, v in self.results.items():
            if k in q:
                return v
        return []

    def video_stats(self, ids):
        self.spent += 1
        return {i: self.stats[i] for i in ids if i in self.stats}


def main():
    import demand_research as dr
    check("matches the series by its distinctive words",
          dr.matches("Murim Psychopath Chapter 44 Recap — He Smiles", ["Murim Psychopath"]))
    check("...or by an alias", dr.matches("Crazy Demon full recap", ["Murim Psychopath", "Crazy Demon"]))
    check("...but not an unrelated recap", not dr.matches("Solo Leveling recap", ["Murim Psychopath"]))

    yt = FakeYT({"Murim Psychopath": [
        {"title": "Murim Psychopath recap part 1", "channel": "A", "video_id": "v1", "published_at": iso(10)},
        {"title": "Murim Psychopath ch 40-44", "channel": "B", "video_id": "v2", "published_at": iso(400)},
        {"title": "Murim Psychopath explained", "channel": "C", "video_id": "v3", "published_at": iso(30)},
        {"title": "Some other manhwa recap", "channel": "D", "video_id": "v4", "published_at": iso(5)}],
                 "Quiet Series": [{"title": "Quiet Series recap", "channel": "E", "video_id": "q1", "published_at": iso(100)}]},
               {"v1": {"views": 20000}, "v2": {"views": 400000}, "v3": {"views": 30000},
                "v4": {"views": 9999999}, "q1": {"views": 500}})
    m = dr.measure({"id": "murim", "title": "Murim Psychopath", "tier": "watchlist"}, yt, NOW)
    check("only matching recaps are counted (the unrelated hit is ignored)", m["recaps"] == 3)
    check("demand is the median views per DAY (2000, 1000, 1000 -> 1000)", m["median_vpd"] == 1000)
    check("high demand -> suggest Make now, and it differs from the current tier",
          m["level"] == "high" and m["suggested_tier"] == "greenlight" and m["differs"])
    check("recent recaps (90 days) are counted", m["recent"] == 2)
    check("the best recaps are listed", m["top"][0]["video_id"] == "v1")
    q = dr.measure({"id": "q", "title": "Quiet Series", "tier": "watchlist"}, yt, NOW)
    check("low demand -> Watching (same tier, so no suggestion)", q["level"] == "low" and not q["differs"])
    n = dr.measure({"id": "n", "title": "Nobody Recaps This", "tier": "greenlight"}, yt, NOW)
    check("no recaps found -> unknown, no suggestion", n["level"] == "unknown" and n["suggested_tier"] is None)

    root = tempfile.mkdtemp(prefix="dem_")
    check("due when never run", dr.due(root, NOW))

    class Boom(FakeYT):
        def search_recaps(self, q, limit=8):
            if "Broken" in q:
                raise RuntimeError("quota exceeded")
            return super().search_recaps(q, limit)
    yt2 = Boom(yt.results, yt.stats)
    out = dr.run(root, [{"id": "murim", "title": "Murim Psychopath", "tier": "watchlist"},
                        {"id": "broken", "title": "Broken", "tier": "watchlist"},
                        {"id": "q", "title": "Quiet Series", "tier": "watchlist"}], yt2, NOW)
    check("one failing series doesn't stop the rest", out["series"]["q"]["level"] == "low"
          and out["series"]["broken"]["level"] == "error")
    check("quota used is recorded", out["quota_used"] == 100 * 2 + 2)
    check("not due again for a week", not dr.due(root, NOW + 6 * 86400) and dr.due(root, NOW + 7 * 86400))
    check("it never changes a tier by itself (suggestions only)", "update_series" not in open(os.path.join(HERE, "demand_research.py")).read())

    # board carries it; the weekly step is skipped without a key
    import ingest
    import server
    import watchlist
    from fastapi.testclient import TestClient
    saved = ingest.PROJECTS
    ingest.PROJECTS = root
    watchlist.save(root, {"series": [{"id": "murim", "title": "Murim Psychopath", "aliases": [], "tier": "watchlist",
                                      "rank": 1, "mirrors": []}]})
    try:
        c = TestClient(server.app)
        b = c.get("/api/series/board").json()
        row = next(x for x in b["series"] if x["id"] == "murim")
        check("the Series board shows the demand and the suggestion",
              row["demand"]["level"] == "high" and row["demand"]["differs"])
        os.environ.pop("YOUTUBE_API_KEY", None)
        import yt_api
        if not yt_api.configured():
            check("without a YouTube key the weekly step does nothing", server._demand_pass() is None)
            check("...and 'check demand' explains why", c.post("/api/demand/run").status_code == 409)
    finally:
        ingest.PROJECTS = saved
    sb = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    check("the board has the demand line, apply and check buttons",
          "sbDemand(x)" in sb and "demandApply(" in sb and "demandRun(this)" in sb)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
