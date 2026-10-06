"""SEO research like Scrapper (owner, 2026-10-05): Google-grounded web research
per series with graded sources, a YouTube search for the series itself, both
fed to the SEO writer, and a credit line from official/trusted facts only.
No network: Gemini, YouTube and the SEO model are faked."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"
R = []


def check(name, ok):
    R.append((name, bool(ok)))


SOURCES = [{"domain": "webtoons.com", "tier": "official"}, {"domain": "mangaupdates.com", "tier": "trusted"},
           {"domain": "regressor.fandom.com", "tier": "wiki"}, {"domain": "reddit.com", "tier": "low"},
           {"domain": "someblog.net", "tier": "other"}]


def main():
    import seo_research as sw
    import seo
    facts = sw.apply_rules({
        "english_title": {"value": "A Regressor's Tale of Cultivation", "sources": [1]},
        "korean_title": {"value": "회귀자의 수선기", "sources": [3]},
        "author": {"value": "Amazing", "sources": [5]},            # only an unlisted blog: not a credit
        "artist": {"value": "Kim Muhyeon", "sources": [2]},
        "platform": {"value": "Naver Webtoon", "sources": [1]},
        "search_names": [{"value": "Regressor Cultivation", "sources": [3]}, {"value": "Reddit name", "sources": [4]}],
        "keywords": [{"value": "murim", "sources": [5]}, {"value": "spam", "sources": [4]}],
    }, SOURCES)
    check("credits need an official or trusted source", facts["english_title"] and facts["artist"] == "Kim Muhyeon" and facts["author"] == "")
    check("search names and keywords may come from wikis and other sites", facts["search_names"] == ["Regressor Cultivation"] and facts["keywords"] == ["murim"])
    check("forums and social never count", "Reddit name" not in facts["search_names"] and "spam" not in facts["keywords"])
    line = sw.credit_line(facts)
    check("credit line from sourced facts, no URL",
          line.startswith("Original work: A Regressor's Tale of Cultivation (회귀자의 수선기) by Kim Muhyeon, published on Naver Webtoon")
          and "http" not in line)
    check("no credit line without an official English title", sw.credit_line({"author": "x"}) == "")

    root = tempfile.mkdtemp(prefix="sw_")
    calls = []

    def fake_research(series, aliases, url, key):
        calls.append(series)
        return {"at": time.time(), "series": series, "sources": SOURCES, "facts": facts, "error": None}
    r1 = sw.get(root, "Regressor", [], "", "key", _research=fake_research)
    r2 = sw.get(root, "Regressor", [], "", "key", _research=fake_research)
    check("researched once, then reused for a week", calls == ["Regressor"] and r2.get("from_cache"))
    sw.get(root, "Regressor", [], "", "key", force=True, _research=fake_research)
    check("Re-read forces a fresh search", calls == ["Regressor", "Regressor"])
    check("no key and nothing saved: none", sw.get(root, "Other", [], "", None) is None)
    path = os.path.join(root, sw.CACHE_DIR, "regressor.json")
    rec = json.load(open(path)); rec["at"] = 0; json.dump(rec, open(path, "w"))
    kept = sw.get(root, "Regressor", [], "", "key", _research=lambda *a: {"error": "boom", "facts": {}})
    check("a failed refresh keeps the saved research", kept["facts"]["english_title"] and kept.get("refresh_error") == "boom")

    class YT:
        def __init__(self): self.qs = []
        def search_recaps(self, q, limit=8):
            self.qs.append(q); return [{"title": f"{q} video", "channel": "c", "video_id": "v1"}]
        def video_stats(self, ids): return {"v1": {"views": 1234}}
    yt = YT()
    card = {"series": "A Regressors Tale Of Cultivation", "genre": ["murim"], "web_facts": facts}
    res = seo.research(yt, card)
    check("YouTube is searched for THIS series (by its official title) as well as the niche",
          any("A Regressor's Tale of Cultivation manhwa recap" == q for q in yt.qs) and len(yt.qs) == 2
          and res["series"]["top"][0]["views"] == 1234)
    seen = {}

    def fake_model(prompt, model):
        seen["p"] = prompt
        return json.dumps({"titles": [{"text": "T", "why": "", "recommended": True}], "description": "D",
                           "description_short": "d", "tags": ["a"], "hashtags": ["#a"], "reasoning": "", "detected": {}})
    d = tempfile.mkdtemp(prefix="swp_")
    seo.generate(d, "x.mp4", {**card, "aliases": []}, {}, res, _call=fake_model)
    check("the SEO writer gets the web research and the series' YouTube results",
          "WHAT THE WEB SAYS ABOUT THIS SERIES" in seen["p"] and "Regressor Cultivation" in seen["p"]
          and "WHAT RANKS ON YOUTUBE FOR THIS SERIES" in seen["p"] and "1234" in seen["p"])
    src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    check("generation adds the credit line and shows the web sources",
          "credit_line((web or {}).get(\"facts\"))" in src and '"web": {"label": "Web research (Google)"' in src)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
