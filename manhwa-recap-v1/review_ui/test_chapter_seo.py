"""docs/audit/10_SEO_SYSTEM_SPEC.md — the per-chapter package, checked against
the spec's own worked example (§8, Murim Psychopath ch.45).
Run: python3 -m pytest test_chapter_seo.py -q"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chapter_seo as cs  # noqa: E402

S = "Murim Psychopath"
PACK = {"series_name_en": S, "series_name_alt": ["Psychopath in Murim", "Murim Sociopath", "Welcome to Murim Online"],
        "series_name_ko": "싸이코패스 in 무림", "authors": {"story": ["Gonbung", "Kim Eon"], "art": ["Song Beom-Gyu"]},
        "publisher": "Naver Webtoon / Naver Series", "characters": ["Dong Bongsu"],
        "seasons": [{"n": 1, "name": "Madman's Paradise"}],
        "genres": ["Murim", "Martial Arts", "Action", "Isekai", "Reincarnation", "Psychological"]}
LOCK = {"series_hook": "He Thought Murim Was Just a Game"}


def test_title_matches_the_worked_example():
    t = cs.build_title(45, LOCK, S)
    assert t == "[45] He Thought Murim Was Just a Game — Murim Psychopath | Manhwa Recap"
    assert len(t) == 71 and cs.hook_budget(S, 45) == 33          # spec: hook <= 33 for this series
    assert cs.build_title(46, LOCK, S).replace("[46]", "[45]") == t   # T6: only the number changes
    # long series name -> short fallback, suffix never cut
    long_s = "The Return Of The Disaster-Class Hero Who Was Betrayed By Everyone"
    t2 = cs.build_title(1125, {"series_hook": "He Thought Murim Was Just a Game"}, long_s)
    assert t2 == "[1125] He Thought Murim Was Just a Game | Manhwa Recap" and len(t2) <= 72


def test_description_lines_frozen_blocks_and_hashtags():
    d = cs.build_description(S, 45, PACK, chapter_title="Intensifying by the Minute",
                             tease="His VR logout failed and dropped him into the real Murim — and now every kill makes him stronger.",
                             prev_n=44, prev_link="https://youtu.be/prev", playlist_link="https://www.youtube.com/playlist?list=PL1")
    lines = d.split("\n")
    assert lines[0] == ('Murim Psychopath Chapter 45 recap (English) — "Intensifying by the Minute"! His VR logout failed '
                        'and dropped him into the real Murim — and now every kill makes him stronger. Full chapter 45 recap on Flamingo Recap!')
    assert lines[1] == "Watch Chapter 44 here: https://youtu.be/prev • Start from Chapter 1: https://www.youtube.com/playlist?list=PL1"
    for blk in (cs.KOFI, cs.SUBSCRIBE, cs.DISCLAIMER, cs.FAIR_USE):
        assert blk in d                                            # D3: byte-identical
    assert "kymedia.mgmt@gmail.com" in d and "🎵" not in d          # no music credit -> no music block
    assert d.rstrip().endswith("#ManhwaRecap #MurimPsychopath #MangaRecap")
    assert "📖 Series: Murim Psychopath (Psychopath in Murim, Murim Sociopath, Welcome to Murim Online / 싸이코패스 in 무림)" in d
    assert cs.validate({"title": cs.build_title(45, LOCK, S), "description": d,
                        "tags": cs.build_tags(S, 45, PACK)}, S, 45) == []


def test_tags_three_blocks_under_500_chapter_block_never_trimmed():
    tags = cs.build_tags(S, 45, PACK)
    assert tags[:6] == ["murim psychopath chapter 45", "murim psychopath ch 45", "murim psychopath 45",
                        "murim psychopath chapter 45 english", "murim psychopath new chapter",
                        "murim psychopath latest chapter"]
    assert "싸이코패스 in 무림" in tags and "psychopath in murim" in tags and "dong bongsu" in tags   # H2
    assert sum(map(len, tags)) + len(tags) - 1 <= 500
    big = dict(PACK, series_name_alt=[f"alt name number {i}" for i in range(40)])
    t2 = cs.build_tags(S, 45, big)
    assert t2[:6] == tags[:6] and sum(map(len, t2)) + len(t2) - 1 <= 500
    assert "flamingo recap" not in t2                              # evergreen tail trimmed first


def test_validation_catches_the_rule_breaks():
    good = {"title": cs.build_title(45, LOCK, S), "description": cs.build_description(S, 45, PACK),
            "tags": cs.build_tags(S, 45, PACK)}
    assert cs.validate(good, S, 45) == []
    bad = dict(good, title="Murim Psychopath Chapter 45 — His Class Change Shocks Murim | Manhwa Recap")
    assert any("T1" in p for p in cs.validate(bad, S, 45))
    walled = dict(good, description=good["description"] + " #a #b #c")
    assert any("H3" in p for p in cs.validate(walled, S, 45))
    counted = dict(good, description=good["description"].replace("Full chapter", "234 novel chapters. Full chapter"))
    assert any("D5" in p for p in cs.validate(counted, S, 45))
    edited = dict(good, description=good["description"].replace("promptly address", "address"))
    assert any("D3" in p for p in cs.validate(edited, S, 45))


def test_hook_rules_and_timecodes():
    assert cs.hook_problems("His Class Change Shocks Murim", S)            # blacklisted
    assert cs.hook_problems("He Thought Murim Was Just a Game", S) == []
    assert cs.hook_problems("Chapter 45 Changes Everything", S)
    ok = [{"t": "0:00", "label": "Recap start"}, {"t": "4:12", "label": "The trap"}, {"t": "8:40", "label": "The duel"}]
    assert cs.valid_timecodes(ok, 600) == ok
    assert cs.valid_timecodes(ok[:2], 600) == []                            # < 3
    assert cs.valid_timecodes([dict(ok[0], t="0:05")] + ok[1:], 600) == []  # not from 0:00
    assert cs.valid_timecodes(ok[:2] + [{"t": "4:15", "label": "x"}], 600) == []   # < 10 s
    assert cs.valid_timecodes(ok[:2] + [{"t": "8:40", "label": "Part 3"}], 600) == []
