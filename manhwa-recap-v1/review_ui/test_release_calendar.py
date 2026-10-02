"""Tracker release calendar: dates and covers parsed from series pages,
merged safely into the watchlist, served and shown. Small synthetic HTML only
(no scraped pages in the repo); the fetcher is mocked, no network."""
import datetime
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import providers as P  # noqa: E402

R = []


def check(name, ok):
    R.append((name, bool(ok)))


WT = """<meta property="og:image" content="https://swebtoon-phinf.pstatic.net/x/poster.jpg?type=crop540_540">
<li class="_episodeItem" id="episode_131" data-episode-no="131"><a href="/viewer?title_no=5988&episode_no=131">
<span class="subj"><span>Episode 131</span></span><span class="date">Sep 29, 2026</span></a></li>
<li class="_episodeItem" id="episode_130" data-episode-no="130"><a href="/viewer?title_no=5988&episode_no=130">
<span class="date">Sep 22, 2026</span></a></li>"""

AS = """<meta property="og:image" content="https://cdn.asurascans.com/covers/murim.webp">
<a href="/comics/murim-psychopath-1/chapter/45" class="x"><span>Chapter <!-- -->45</span><span class="text-sm">3 days ago</span></a>
<a href="/comics/murim-psychopath-1/chapter/44" class="x"><span>Chapter 44</span><span class="text-sm">last week</span></a>
<a href="/comics/murim-psychopath-1/chapter/40" class="x"><span>Chapter 40</span><span class="text-sm">Sep 1, 2026</span></a>"""


def main():
    today = datetime.date(2026, 10, 2)
    w = P.release_info(P.by_name("webtoon"), WT, today)
    check("WEBTOON: cover from og:image", w["cover"].startswith("https://swebtoon-phinf.pstatic.net/"))
    check("WEBTOON: exact episode dates", w["dates"] == {"131": ["2026-09-29", False], "130": ["2026-09-22", False]})
    a = P.release_info(P.by_name("asura"), AS, today)
    check("Asura: absolute dates are exact", a["dates"]["40"] == ["2026-09-01", False])
    check("Asura: '3 days ago' is an approximate date", a["dates"]["45"] == ["2026-09-29", True])
    check("Asura: 'last week' is an approximate date", a["dates"]["44"] == ["2026-09-25", True])
    check("unparseable text is ignored", P._parse_date("Chapter 45", today) is None)

    # merge rules inside the watchlist check
    import watchlist as W
    root = tempfile.mkdtemp(prefix="wl_")
    W.add_series(root, "Murim Psychopath")
    W.add_mirror(root, W.load(root)["series"][0]["id"], "https://asurascans.com/comics/murim-psychopath-1/")
    data = W.load(root)
    s = data["series"][0]
    key = s["mirrors"][0]["series_key"]
    s["mirrors"][0]["release_dates"] = {"45": ["2026-09-28", False]}   # an exact date already known
    W.save(root, data)
    m = W.refresh_mirror(root, s["id"], key, _fetcher=lambda u: AS)
    check("the check stores release dates and the cover",
          m["status"] == "ok" and m["cover"].endswith("murim.webp") and "44" in m["release_dates"])
    check("an exact date is never replaced by an approximate one",
          m["release_dates"]["45"] == ["2026-09-28", False])
    calls = []
    W.refresh_mirror(root, s["id"], key, _fetcher=lambda u: calls.append(u) or AS)
    check("chapters and dates share ONE page download", len(calls) == 1)

    src = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    check("the Tracker opens on the release calendar", "trkTab('cal')" in src and 'id="trk_cal"' in src)
    check("calendar cards ingest through the watchlist (no quoted args)",
          "onclick=\"wlIngest(this.dataset.sid, this.dataset.key, this.dataset.ch)\"" in src)
    check("approximate dates are marked ≈", "(i.approx ? '≈ ' : '')" in src)

    for n, ok in R:
        print(("PASS " if ok else "FAIL ") + n)
    n_ok = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n_ok, len(R)))
    return 0 if n_ok == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
