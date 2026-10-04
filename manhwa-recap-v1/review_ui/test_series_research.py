"""Story research (owner, 2026-10-04): a sourced Series Bible for every series.
Source grading, the owner's fan-wiki rule, merge (hand-written / owner edits
win), the grounded call with the real response shape, name suggestions from
our own scripts, and the routes. Fake network and temp dirs; no money."""
import io
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


GROUNDED = {"candidates": [{"content": {"parts": [{"text": "Yu Shin is the protagonist. Yeon Yeong-ha is a woman."}]},
                            "groundingMetadata": {
                                "groundingChunks": [{"web": {"uri": "https://en.wikipedia.org/wiki/Murim", "title": "wikipedia.org"}},
                                                    {"web": {"uri": "https://murim.fandom.com/wiki/Yu_Shin", "title": "fandom.com"}},
                                                    {"web": {"uri": "https://www.reddit.com/r/x", "title": "reddit.com"}}],
                                "groundingSupports": [{"segment": {"text": "Yu Shin is the protagonist."}, "groundingChunkIndices": [0, 1]}],
                                "webSearchQueries": ["murim psychopath characters"]}}],
            "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 60, "thoughtsTokenCount": 200}}
STRUCT = {"world_setting": {"universe": "Murim", "premise": "A psychopath in the martial world", "sources": [0]},
          "characters": [{"name": "Yu Shin", "aliases": ["Madman"], "gender": "male", "pronouns": "he/him",
                          "role": "protagonist", "visual_cues": "wild dark hair", "sources": [1]},
                         {"name": "Yeon Yeong-ha", "aliases": ["Cursed Killing Star"], "gender": "female",
                          "pronouns": "she/her", "role": "assassin", "sources": [1]},
                         {"name": "Rumour Guy", "gender": "male", "sources": [2]}],
          "story_so_far": {"text": "A festival ambush.", "sources": [1]},
          "disputes": ["Some sources call the Cursed Killing Star a separate person."]}


class Net:
    def __init__(self):
        self.calls = []

    def __call__(self, req, timeout=None, context=None):
        body = json.loads(req.data)
        self.calls.append(body)
        if body.get("tools"):
            return io.BytesIO(json.dumps(GROUNDED).encode())
        return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(STRUCT)}]}}],
                                      "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 300}}).encode())


def main():
    import usage
    root = tempfile.mkdtemp(prefix="sr_")
    usage.USAGE_DIR = root
    usage.COUNTS_PATH = os.path.join(root, "counters.json")
    usage.LOG_PATH = os.path.join(root, "calls.log.jsonl")
    usage.LOCK_PATH = os.path.join(root, ".lock")
    import series_research as sr

    check("webtoons.com is official", sr.tier("www.webtoons.com") == "official")
    check("wikipedia / anilist are trusted", sr.tier("en.wikipedia.org") == "trusted" and sr.tier("anilist.co") == "trusted")
    check("fan wikis are 'wiki'", sr.tier("murim.fandom.com") == "wiki")
    check("reddit / youtube are low", sr.tier("reddit.com") == "low" and sr.tier("youtube.com") == "low")

    net = Net()
    rec = sr.research("Murim Psychopath", ["Crazy Demon"], "https://asura/x", "45", "43 to 45", "AQ.x",
                      _urlopen=net, _resolve=lambda u: u)
    check("research uses Google Search grounding", net.calls[0].get("tools") == [{"google_search": {}}])
    check("...then a structuring call without tools", len(net.calls) == 2 and not net.calls[1].get("tools"))
    check("sources are graded", [s["tier"] for s in rec["sources"]] == ["trusted", "wiki", "low"])
    check("claims keep their source indexes", rec["claims"][0]["sources"] == [0, 1])
    rows = [json.loads(l) for l in open(usage.LOG_PATH)]
    check("both calls are metered with real tokens (thinking included)",
          len(rows) == 2 and rows[0]["metered"] and rows[0]["output_tokens"] == 260)

    b = sr.to_bible("Murim Psychopath", ["Crazy Demon"], rec)
    names = [c["name"] for c in b["characters"]]
    check("fan-wiki-backed names and pronouns are kept (owner rule)", "Yu Shin" in names and "Yeon Yeong-ha" in names)
    check("a character backed only by low sources is rejected", "Rumour Guy" not in names
          and b["research"]["rejected_characters"] == ["Rumour Guy"])
    check("world facts need an official/trusted source (wikipedia: yes)", b["world_setting"].get("universe") == "Murim")
    check("story so far from a fan wiki only is kept but marked unverified",
          b["research"]["story_so_far"] and b["research"]["story_verified"] is False)
    check("disputes are kept for the owner", b["research"]["disputes"])

    hand = {"canonical_title": "Murim Psychopath", "aliases": ["Crazy Demon"],
            "world_setting": {"universe": "Murim / Martial Arts", "costume_vs_monster_rule": "lion heads are props"},
            "characters": [{"name": "Yu Shin", "pronouns": "he/him", "visual_cues": "fur collar"},
                           {"name": "Cursed Killing Star", "aliases": ["Lady Assassin"], "pronouns": "she/her"}]}
    m = sr.merge(hand, b)
    by = {c["name"]: c for c in m["characters"]}
    check("merge: the hand-written entry wins (Yu Shin keeps its look)", by["Yu Shin"]["visual_cues"] == "fur collar")
    check("merge: a researched character whose ALIAS is already listed is not duplicated",
          "Yeon Yeong-ha" not in by)
    check("merge: hand-written world and the costume rule are kept",
          m["world_setting"]["universe"] == "Murim / Martial Arts" and m["world_setting"]["costume_vs_monster_rule"])
    check("merge: research record attached", m["research"]["sources"])

    sug = sr.suggest_from_script(m, "Yu Shin smiled. Lord Mubon waited. Later Lord Mubon spoke to Yu Shin. "
                                    "The Elder nodded. Baek Chun ran. Baek Chun fell.")
    check("names the scripts keep using are suggested", "Lord Mubon" in sug and "Baek Chun" in sug)
    check("...but known names and one-off words are not", "Yu Shin" not in sug and "Elder" not in sug)

    # ---- service + routes (temp projects root, fake research)
    import ingest
    import server
    import series_bible
    import watchlist
    from fastapi.testclient import TestClient
    proj = tempfile.mkdtemp(prefix="srp_")
    saved = (ingest.PROJECTS, series_bible.BIBLES_DIR, sr.research)
    ingest.PROJECTS = proj
    series_bible.BIBLES_DIR = os.path.join(proj, "_series_bibles")
    watchlist.save(proj, {"series": [{"id": "the-stellar-swordmaster", "title": "The Stellar Swordmaster",
                                      "aliases": [], "tier": "greenlight", "rank": 1,
                                      "mirrors": [{"series_key": "webtoon:5988", "source": "webtoon",
                                                   "series_url": "https://www.webtoons.com/en/action/the-stellar-swordmaster/list?title_no=5988",
                                                   "support": "supported", "chapters": ["129", "130"], "latest": "130"}]}]})
    sr.research = lambda *a, **k: rec
    try:
        c = TestClient(server.app)
        v = c.get("/api/series/bible", params={"series_id": "the-stellar-swordmaster"}).json()
        check("a series without research shows no bible", v["bible"] is None)
        import research_service
        research_service.build("the-stellar-swordmaster", api_key="AQ.x")
        slug = research_service._slugs(research_service._series("the-stellar-swordmaster"))[0]
        check("the bible is saved under the slug ingest looks up (WEBTOON keeps its title number)",
              slug == "the-stellar-swordmaster-5988" and series_bible.load_series_bible(slug))
        v = c.get("/api/series/bible", params={"series_id": "the-stellar-swordmaster"}).json()
        check("the route returns the researched bible", v["bible"]["research"]["sources"])
        e = dict(v["bible"]); e["characters"] = [{"name": "Ian Page", "pronouns": "he/him"}]
        r = c.post("/api/series/bible", json={"series_id": "the-stellar-swordmaster", "bible": e})
        check("owner edits are saved and marked as theirs",
              r.status_code == 200 and r.json()["bible"]["characters"][0]["origin"] == "owner")
        r = c.post("/api/series/research", json={"missing_only": True})
        check("'research series without a cast list' skips ones that have one", r.json().get("job") is None)
        check("unknown series -> 404", c.get("/api/series/bible", params={"series_id": "nope"}).status_code == 404)
    finally:
        ingest.PROJECTS, series_bible.BIBLES_DIR, sr.research = saved

    src = open(os.path.join(HERE, "ingest.py"), encoding="utf-8").read()
    check("ingest researches a series that has no bible before describing it",
          "research_service.build_for_url(url)" in src)
    check("ingest notes names the new script uses", "research_service.note_suggestions(url, script)" in src)


if __name__ == "__main__":
    main()
    bad = [n for n, ok in R if not ok]
    for n, ok in R:
        print(("  PASS " if ok else "  FAIL ") + n)
    print(f"\n{len(R) - len(bad)}/{len(R)} passed")
    sys.exit(1 if bad else 0)
