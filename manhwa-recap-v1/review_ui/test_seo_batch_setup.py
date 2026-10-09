"""Unit test for Spec-10 Phase B prompts, research_series_seo, and /api/seo/batch_setup."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["AUTOPILOT_SCHEDULER"] = "0"

import series_research as sr
import series_pack as sp
import series_hooks as sh
import chapter_seo as cs

R = []
def check(name, ok):
    R.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}")


def test_prompts():
    p = sr.seo_research_prompt("Murim Psychopath", ["Crazy Demon"], "https://asuracomic.net/series/murim", 45)
    check("seo_research_prompt includes title, alias, url, latest",
          "Murim Psychopath" in p and "Crazy Demon" in p and "asuracomic.net" in p and "45" in p)
    check("seo_research_prompt has 10 numbered sections and rules",
          "10. YOUTUBE RECON" in p and "RULES:" in p and "NOT FOUND" in p)

    sp_prompt = sr.seo_structure_prompt("Murim Psychopath", "research text here", "[0] mangaupdates.com (trusted)")
    check("seo_structure_prompt targets JSON with required fields",
          "series_name_en" in sp_prompt and "characters_main" in sp_prompt and "synopsis_verbatim" in sp_prompt)


def test_research_series_seo_murim():
    # Mock data for Murim Psychopath
    murim_text = """1. Canonical English title: Murim Psychopath. Medium: Korean manhwa.
2. Official original-language title: 싸이코패스 in 무림 (Naver Webtoon).
3. Associated titles: Psychopath in Murim, Murim Sociopath, Welcome to Murim Online.
4. Story credits: Gonbung, Kim Eon (also romanized Kim Yeon). Art: Song Beom-Gyu.
5. Original publisher: Naver Webtoon.
6. Genres: Action, Martial Arts, Murim, Psychological. Tag phrases: murim manhwa, martial arts manhwa.
7. Main characters: Dong Bongsu (Protagonist).
8. Season 1: Madman's Paradise (chapters 1-38). Latest chapter: 46 (released 2026-10-01).
9. Official synopsis: When he tried returning to virtual reality, he found himself trapped in the real Murim.
10. YouTube recon: He Thought Murim Was Just a Game (Manhwa Chatter, 159K views)."""

    fake_struct = {
        "series_name_en": "Murim Psychopath",
        "medium": "manhwa",
        "series_name_original": {"value": "싸이코패스 in 무림", "sources": [0]},
        "series_name_alt": ["Psychopath in Murim", "Murim Sociopath", "Welcome to Murim Online"],
        "authors": {"story": {"value": ["Gonbung", "Kim Eon"], "sources": [0], "conflicts": ["Kim Yeon"]},
                     "art": {"value": ["Song Beom-Gyu"], "sources": [0]}},
        "publisher": {"value": "Naver Webtoon", "sources": [0]},
        "genres": ["Action", "Martial Arts", "Murim", "Psychological"],
        "genre_tag_phrases": ["murim manhwa", "martial arts manhwa"],
        "characters_main": ["Dong Bongsu"],
        "seasons": [{"n": 1, "name": "Madman's Paradise", "chapters": "1-38"}],
        "latest_chapter": {"number": 46, "released_utc": "2026-10-01", "sources": [0]},
        "synopsis_verbatim": {"value": "When he tried returning to virtual reality, he found himself trapped in the real Murim.", "sources": [0]},
        "youtube_recon": [{"title": "He Thought Murim Was Just a Game", "channel": "Manhwa Chatter", "views": 159000, "date": "2024-01-01", "style": "hook", "weakness": ""}],
        "gap": "Exact-phrase + misconception hook is unoccupied in the top 3 results."
    }

    call_count = [0]
    def fake_post(body, api_key, _urlopen=None):
        call_count[0] += 1
        if call_count[0] == 1:
            # call 1: grounded research
            return {
                "candidates": [{
                    "content": {"parts": [{"text": murim_text}]},
                    "groundingMetadata": {
                        "webSearchQueries": ["Murim Psychopath mangaupdates"],
                        "groundingChunks": [{"web": {"uri": "https://mangaupdates.com/series/murim", "title": "MangaUpdates"}}],
                        "groundingSupports": [{"segment": {"text": murim_text}, "groundingChunkIndices": [0]}]
                    }
                }]
            }
        else:
            # call 2: JSON structuring
            return {
                "candidates": [{
                    "content": {"parts": [{"text": json.dumps(fake_struct)}]}
                }]
            }

    pack = {"series_name_en": "Murim Psychopath", "source_url": "https://asuracomic.net/series/murim"}
    # monkeypatch _post in series_research
    orig_post = sr._post
    try:
        sr._post = fake_post
        res = sr.research_series_seo("murim-psychopath", pack, api_key="fake-key")
    finally:
        sr._post = orig_post

    check("research_series_seo made two calls", call_count[0] == 2)
    check("research_series_seo returned structured data with canonical title", res["structured"]["series_name_en"] == "Murim Psychopath")
    check("characters_main is separated from series_name_alt", "Dong Bongsu" in res["structured"]["characters_main"] and "Dong Bongsu" not in res["structured"]["series_name_alt"])
    check("synopsis_verbatim is present", "virtual reality" in res["structured"]["synopsis_verbatim"]["value"])
    check("facts backward compatibility populated", res["facts"]["author"] == "Gonbung" and res["facts"]["platform"] == "Naver Webtoon")


def test_owner_keys_and_batch_setup():
    check("seo_research is in OWNER_KEYS", "seo_research" in sp.OWNER_KEYS)

    with tempfile.TemporaryDirectory() as tmp:
        import server
        import ingest as _i

        # Setup test directory with two series
        p1 = os.path.join(tmp, "murim_45")
        os.makedirs(p1)
        with open(os.path.join(p1, "project.json"), "w") as f:
            json.dump({"series": "Murim Psychopath", "chapter": 45, "url": "https://asurascans.com/comics/murim-psychopath/chapter/45"}, f)

        p2 = os.path.join(tmp, "locked_10")
        os.makedirs(p2)
        with open(os.path.join(p2, "project.json"), "w") as f:
            json.dump({"series": "Locked Series", "chapter": 10, "url": "https://asurascans.com/comics/locked-series/chapter/10"}, f)

        orig_proj = _i.PROJECTS
        _i.PROJECTS = tmp
        try:
            # 1. Series 2 already locked
            sp.save(tmp, {
                "series_id": "locked-series",
                "series_name_en": "Locked Series",
                "title_lock": {"series_hook": "He Did X", "approved_by_user": True}
            })

            # 2. Fake generate for hooks
            def fake_generate(series, pack, bible, web, key):
                return {"at": time.time(), "candidates": [
                    {"hook": "He Thought Murim Was Just a Game", "mechanic": 5,
                     "example_title": "[45] He Thought Murim Was Just a Game — Murim Psychopath | Manhwa Recap",
                     "provenance": ["https://mangaupdates.com"], "why": "misconception"}
                ]}

            # 3. Fake research_series_seo
            def fake_research(series_id, pack, api_key=None):
                return {
                    "at": time.time(),
                    "structured": {"series_name_en": "Murim Psychopath", "synopsis_verbatim": {"value": "A VR game"}},
                    "facts": {"keywords": ["murim-psychopath"]}
                }

            orig_gen = sh.generate
            orig_rs = sr.research_series_seo
            sh.generate = fake_generate
            sr.research_series_seo = fake_research
            try:
                # First run
                resp = server.api_seo_batch_setup(background=False)
                check("batch_setup returns ok", resp.get("ok") is True)
                results = {r["series"]: r for r in resp.get("results", [])}
                check("locked series was skipped", "skipped: hook already locked" in results.get("locked-series", {}).get("action", ""))
                check("murim got hooks generated", "hooks ready" in results.get("murim-psychopath", {}).get("action", ""))

                # Verify murim pack on disk has hook_candidates and seo_research
                m_pack = sp.load(tmp, "murim-psychopath")
                check("murim pack saved hook_candidates", bool(m_pack.get("hook_candidates")))
                check("murim pack saved seo_research", bool(m_pack.get("seo_research")))
                check("murim hook was NEVER auto-locked", not (m_pack.get("title_lock") or {}).get("approved_by_user"))

                # Second run: set playlist_id on murim
                m_pack["playlist_id"] = "PL1234567890"
                sp.save(tmp, m_pack)

                resp2 = server.api_seo_batch_setup(background=False)
                results2 = {r["series"]: r for r in resp2.get("results", [])}
                check("idempotency: locked series still skipped", "skipped: hook already locked" in results2.get("locked-series", {}).get("action", ""))
                check("idempotency: murim skipped (playlist set + awaiting owner pick)", "awaiting owner pick" in results2.get("murim-psychopath", {}).get("action", ""))

            finally:
                sh.generate = orig_gen
                sr.research_series_seo = orig_rs
        finally:
            _i.PROJECTS = orig_proj


if __name__ == '__main__':
    test_prompts()
    test_research_series_seo_murim()
    test_owner_keys_and_batch_setup()
    fails = [name for name, ok in R if not ok]
    print(f'\n{len(R) - len(fails)}/{len(R)} passed')
    if fails:
        sys.exit(1)
