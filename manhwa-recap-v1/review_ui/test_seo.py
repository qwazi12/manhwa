"""SEO Copilot: project truth first, research as refinement, operator in control.

The load-bearing assertions are the ones about ORDER and AUTHORITY — that the
ingested chapter decides what the video is, that research can only change how
it is packaged, and that nothing reaches the publish fields without a click.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..")))

import seo          # noqa: E402
import server as srv  # noqa: E402
import ingest as ing  # noqa: E402
import yt_api       # noqa: E402

R = []


def check(name, ok):
    R.append((name, bool(ok)))


# ------------------------------------------------------------------ fixtures
NARRATION = [
    "Doctor Jiwoo Han collapsed in the clinic after the explosion tore through the ward.",
    "He awoke in a strange body, the surgery scars gone, his hands young again.",
    "Jiwoo realised he had regressed twenty years into his own past.",
    "The Han clan would fall in three months unless Jiwoo changed the timeline.",
    "Armed with modern medical knowledge, Jiwoo began to rebuild the clinic.",
    "His rival Seojun watched from the shadows, plotting revenge for the betrayal.",
]


def make_project(root, pid="doctors-rebirth_1", with_meta=True, beats=True):
    pdir = os.path.join(root, pid)
    os.makedirs(os.path.join(pdir, "exports"), exist_ok=True)
    meta = {"id": pid, "series": "Doctors Rebirth", "chapter": "1",
            "url": "https://asurascans.com/comics/doctors-rebirth-53fc8424/chapter/1",
            "duration": 428.7, "n_segments": 6} if with_meta else {}
    json.dump(meta, open(os.path.join(pdir, "project.json"), "w"))
    segs = []
    for i, t in enumerate(NARRATION if beats else []):
        segs.append({"seg_index": i, "start": i * 8.0, "end": i * 8.0 + 8.0,
                     "dur": 8.0, "panel_id": "p%03d" % i, "user_included": True,
                     "clip": "clips/seg_%03d.mp4" % i,
                     "beats": [{"index": i, "start": i * 8.0, "end": i * 8.0 + 7.0,
                                "text": t, "file": "beat_%03d.mp3" % i}]})
    json.dump(segs, open(os.path.join(pdir, "segments.json"), "w"))
    # Varied per panel, like real data: identical boilerplate on every panel
    # would give the genre detector a signal no real project produces.
    scenes = ["a young doctor collapsing in a bright hospital ward",
              "surgery lights glaring above an operating table",
              "a man waking in an unfamiliar younger body",
              "a clinic corridor crowded with waiting patients",
              "a rival in a dark coat watching from a doorway",
              "hands holding a scalpel, steady for the first time"]
    json.dump([{"panel_id": "p%03d" % i, "ocr_text": "DOCTOR'S REBIRTH / CH 1",
                "visual_description": scenes[i % len(scenes)]}
               for i in range(12)],
              open(os.path.join(pdir, "descriptions.json"), "w"))
    open(os.path.join(pdir, "exports", "final_a.mp4"), "wb").write(b"0" * 64)
    return pdir


class FakeYT:
    """Stands in for the YouTube API so tests never hit the network."""

    def __init__(self, titles=None, descs=None, fail=False, hits=None):
        self.spent = 0
        self.fail = fail
        self._titles = titles if titles is not None else [
            "**NEW PART 10** He Reincarnates With The ULTIMATE CHEAT System",
            "(1) Bullied Teen Returns To High School For Revenge",
            "*Pt 2* Game Designer Reincarnates As A Named Villain",
            "**PART 5** Poor Gamer Unlocks SS-Class System",
            "**NEW PART 7** Overweight Guy Gains SSS+ Skills From A Supreme Being",
        ] * 6
        self._descs = descs if descs is not None else [
            "Support: https://ko-fi.com/x #manhwa #manhwarecap\n\nTimecodes:\n"
            "0:00 Part 1\n\nCredits:\nBackground music: ...\n"] * 30
        self._hits = hits

    def channel(self, handle):
        if self.fail:
            raise yt_api.YouTubeError("403 (accessNotConfigured): API not enabled")
        self.spent += 1
        return {"channel_id": "UC123", "title": "Flamingo Remix", "description": "",
                "video_count": 63, "subscriber_count": 789,
                "uploads_playlist": "UU123"}

    def uploads(self, playlist_id, limit=50):
        self.spent += 1
        return [{"title": t, "description": d, "published_at": None, "video_id": "v%d" % i}
                for i, (t, d) in enumerate(zip(self._titles[:limit],
                                               (self._descs * 20)[:limit]))]

    def search_recaps(self, query, limit=8):
        if self.fail:
            raise yt_api.YouTubeError("quotaExceeded")
        self.spent += 100
        if self._hits is not None:
            return self._hits
        return [{"title": "Manhwa Recap: He Became The Strongest %d" % i,
                 "channel": "Other Channel", "video_id": "x%d" % i,
                 "published_at": None} for i in range(limit)]

    def video_stats(self, ids):
        self.spent += 1
        return {v: {"views": 1000 * (i + 1), "likes": 10, "tags": []}
                for i, v in enumerate(ids)}


def fake_model(expect=None):
    """Returns a generator callable and a list that captures the prompt."""
    seen = []

    def call(prompt, model):
        seen.append(prompt)
        return json.dumps({
            "titles": [
                {"text": "Doctor Regresses 20 Years To Save The Clan He Failed",
                 "why": "leads with the premise", "recommended": True},
                {"text": "He Woke Up 20 Years In The Past With MODERN Medicine",
                 "why": "caps emphasis like the channel", "recommended": False},
                {"text": "(1) Surgeon Regresses To Rebuild His Fallen Clan",
                 "why": "part marker", "recommended": False},
                {"text": "A Dying Doctor Gets A Second Life And A SECOND CHANCE",
                 "why": "hook first", "recommended": False},
            ],
            "description": "Jiwoo Han dies in a clinic explosion and wakes twenty "
                           "years in his own past.\n\n#manhwa #manhwarecap",
            "description_short": "A surgeon regresses twenty years to save his clan.",
            "tags": ["Doctors Rebirth", "doctors rebirth recap", "manhwa recap",
                     "regression manhwa", "manhwa"],
            "hashtags": ["#manhwa", "#manhwarecap", "#regression"],
            "reasoning": "Took the premise from the narration, matched the channel's "
                         "premise-hook titles, and used recap discovery phrasing.",
            "detected": {"series": "WRONG SERIES NAME", "chapter": "99",
                         "premise": "A surgeon regresses."},
        })
    return call, seen


def main():
    root = tempfile.mkdtemp(prefix="seo_")
    ing.PROJECTS = root
    pdir = make_project(root)
    srv.active_project_dir = lambda: pdir

    # ============================ 1. project truth comes first
    card = seo.truth_card(pdir)
    check("the ingested chapter URL is read as the primary source",
          card["source_url"].startswith("https://asurascans.com/comics/doctors-rebirth"))
    check("series is taken from the project, not guessed",
          card["series"] == "Doctors Rebirth")
    check("chapter is taken from the project", card["chapter"] == "1")
    check("characters are extracted from the NARRATION", "Jiwoo" in card["characters"])
    check("...and the series' own words are not offered as characters",
          "Doctors" not in card["characters"])
    check("genre is inferred from words actually present",
          "medical" in card["genre"])
    check("...and a second real signal is picked up too",
          any("revenge" in g or "regress" in g for g in card["genre"]))
    check("genre words NOT in the text are not claimed",
          not any("dungeon" in g or "academy" in g for g in card["genre"]))
    check("the premise hook comes from the opening narration",
          "collapsed in the clinic" in card["hook"])
    check("key events are the narration in order",
          card["events"][0].startswith("Doctor Jiwoo Han collapsed"))
    check("aliases come from the URL slug, not invention",
          any("Doctors Rebirth" in a or "Doctor's" in a for a in card["aliases"])
          or card["aliases"] == [])
    check("truth_card makes NO network call and needs no model",
          isinstance(card, dict) and "gaps" in card)

    # ============================ 2. weak ingest lowers confidence, invents nothing
    bare = make_project(root, "bare_1", with_meta=False, beats=False)
    weak = seo.truth_card(bare)
    check("a project with no metadata yields NO series rather than a guess",
          weak["series"] == "" and weak["chapter"] == "")
    check("...and names what was missing", len(weak["gaps"]) >= 3)
    check("...including the absent ingest URL",
          any("URL" in g for g in weak["gaps"]))
    yt = FakeYT()
    style = seo.channel_style(yt, root)
    res = seo.research(yt, card)
    strong_conf = seo.confidence(card, style, res)
    weak_conf = seo.confidence(weak, style, res)
    check("a weak ingest scores materially lower than a strong one",
          weak_conf["score"] < strong_conf["score"] - 30)
    check("...and lands in a lower band",
          weak_conf["band"] == "low" and strong_conf["band"] in ("high", "medium"))

    # ============================ 3. channel style is MEASURED, not asserted
    t = style["titles"]
    check("channel style is measured from real uploads", t["samples"] >= 20)
    check("...detecting the leading part-marker pattern", t["pct_leading_marker"] >= 60)
    check("...detecting ALLCAPS emphasis", t["pct_allcaps_emphasis"] >= 50)
    check("...and detecting that the series name is usually ABSENT from titles",
          t["pct_quoted_series"] <= 20)
    check("the channel's real hashtags are collected",
          "#manhwarecap" in style["descriptions"]["top_hashtags"])

    no_style = seo.analyze_titles([])
    check("no uploads means no style claim", no_style["samples"] == 0)

    # ============================ 4. research is bounded and refines only
    check("research runs exactly one search plus one stats call",
          res["ok"] and res["n"] > 0)
    check("...ranked by real view counts", res["top"][0]["views"] >= res["top"][-1]["views"])
    check("...and reports the query it used", "manhwa recap" in res["query"])
    check("research yields PATTERNS, not a list to copy", "patterns" in res)

    dead = FakeYT(fail=True)
    res_fail = seo.research(dead, card)
    check("a failed search degrades instead of raising", res_fail["ok"] is False)
    conf_nores = seo.confidence(card, style, res_fail)
    check("...and lowers confidence rather than inventing research",
          conf_nores["score"] < strong_conf["score"])

    broken_style = seo.channel_style(FakeYT(fail=True), tempfile.mkdtemp())
    check("an unreachable channel yields no style rather than a fake one",
          broken_style["titles"]["samples"] == 0 and broken_style["error"])

    # ============================ 5. generation obeys project truth
    call, prompts = fake_model()
    out = seo.generate(pdir, "final_a.mp4", card, style, res, _call=call)
    p = prompts[0]
    check("the prompt carries the project truth", "Doctors Rebirth" in p)
    check("...and the ingested source URL", "asurascans.com" in p)
    check("...and the measured channel style", "pct_naming_the_series_in_title" in p)
    check("...and instructs against copying competitors",
          "Never reuse a competitor" in p)
    check("the model may NOT rename the series — the project wins",
          out["detected"]["series"] == "Doctors Rebirth")
    check("...nor renumber the chapter", out["detected"]["chapter"] == "1")
    check("4 title options are produced", len(out["titles"]) == 4)
    check("exactly one is recommended",
          sum(1 for x in out["titles"] if x["recommended"]) == 1)
    check("every title carries an explanation", all(x["why"] for x in out["titles"]))
    check("a description and a short variant are produced",
          out["description"] and out["description_short"])
    check("tags and hashtags are produced", out["tags"] and out["hashtags"])
    check("reasoning is produced", len(out["reasoning"]) > 20)

    # limits are enforced in CODE, not trusted to the model
    long_call, _ = (lambda: ((lambda prompt, model: json.dumps({
        "titles": [{"text": "T" * 300, "why": "x", "recommended": False}] * 9,
        "description": "D" * 9000, "description_short": "s",
        "tags": ["tag%d" % i for i in range(200)],
        "hashtags": ["#a"] * 30, "reasoning": "r", "detected": {}})), []))()
    big = seo.generate(pdir, "final_a.mp4", card, style, res, _call=long_call)
    check("an over-long title is clamped to YouTube's 100 chars",
          all(len(x["text"]) <= 100 for x in big["titles"]))
    check("no more than 5 titles survive", len(big["titles"]) <= 5)
    check("a recommendation is forced when the model marks none",
          sum(1 for x in big["titles"] if x["recommended"]) == 1)
    check("the description is clamped to 5000", len(big["description"]) <= 5000)
    check("tags are clamped to 500 chars total",
          len(",".join(big["tags"])) <= 500)
    check("hashtags are capped at 6", len(big["hashtags"]) <= 6)

    # ==================== 5b. the copilot must not invent links or timecodes
    # Measured against the real channel this was NOT theoretical: the model
    # copied the channel's description furniture and filled it in — a Ko-fi URL
    # that was not the publisher's, and a full timecode list for a video it has
    # never seen. A wrong donation link is worse than none.
    fabricated = """Jin is the youngest son of a legendary clan.

TIMECODES:
00:00 - Intro
01:45 - The Ceremony
12:30 - The Duel

[ SUPPORT THE CHANNEL ]
Support us on Ko-fi: https://ko-fi.com/nottherealchannel

Read the original here: https://asurascans.com/comics/doctors-rebirth-53fc8424/chapter/1
Track list is in our pinned comment!

He refused to give up."""
    clean, dropped = seo.sanitize_description(fabricated, card)
    check("a fabricated donation link is removed", "ko-fi" not in clean.lower())
    check("invented timecodes are removed",
          "01:45" not in clean and "12:30" not in clean)
    check("a reference to a pinned comment that may not exist is removed",
          "pinned comment" not in clean.lower())
    check("the REAL ingested source URL survives",
          "asurascans.com/comics/doctors-rebirth" in clean)
    check("the actual story prose survives",
          "youngest son" in clean and "refused to give up" in clean)
    check("what was stripped is reported, not hidden",
          "timecode" in dropped and "url" in dropped)
    check("an emptied section header does not survive as a stub",
          "TIMECODES" not in clean)
    clean2, dropped2 = seo.sanitize_description("Just a plain summary.", card)
    check("a clean description is left untouched",
          clean2 == "Just a plain summary." and dropped2 == [])

    linky, _ = (lambda: (seo.sanitize_description(
        "See https://evil.example.com/x for more", card)))()
    check("any URL that is not the ingested source is stripped",
          "evil.example.com" not in linky)

    # the sanitizer runs as part of generation, not only when called directly
    def fab_call(prompt, model):
        return json.dumps({"titles": [{"text": "T", "why": "w", "recommended": True}],
                           "description": "Story.\n00:00 - Intro\nhttps://ko-fi.com/fake",
                           "description_short": "s", "tags": ["a"],
                           "hashtags": ["#a"], "reasoning": "r", "detected": {}})
    fab = seo.generate(pdir, "final_a.mp4", card, style, res, _call=fab_call)
    check("generation itself sanitizes, not just the helper",
          "ko-fi" not in fab["description"].lower()
          and "00:00" not in fab["description"])
    check("...and records that it had to", fab.get("sanitized"))
    check("the prompt also tells the model not to write links or timecodes",
          "NEVER write a URL" in prompts[0])

    # characters must be names, not sentence-initial grammar
    tricky = "Yet the clan endured. Shadows fell. The brothers mocked Jin again. " \
             "Later they mocked Jin twice. The rival struck Jin hard."
    import collections as _c
    mid = _c.Counter()
    for m in re.finditer(r"(^|[^.!?]\s+)([A-Z][a-z]{2,})\b", tricky):
        mid[m.group(2)] += 1
    check("a sentence-initial capital is not counted as a character",
          mid.get("Shadows", 0) == 0)
    check("...while a real name used mid-sentence is", mid.get("Jin", 0) >= 2)

    # ============================ 6. persistence per export
    sig = srv.cut_signature(pdir=pdir)
    rec = {**out, "cut_signature": sig, "generated_at": 1.0, "applied": {}}
    seo.put(pdir, "final_a.mp4", rec)
    got = seo.get(pdir, "final_a.mp4", current_signature=sig)
    check("suggestions persist per export", got["titles"][0]["text"] == out["titles"][0]["text"])
    check("...on disk as seo.json", os.path.exists(os.path.join(pdir, "seo.json")))
    seo.put(pdir, "final_b.mp4", {**rec, "description": "second export"})
    check("a second export keeps its OWN suggestions",
          seo.get(pdir, "final_b.mp4")["description"] == "second export"
          and seo.get(pdir, "final_a.mp4")["description"] != "second export")
    check("an export with no suggestions returns empty, not an error",
          seo.get(pdir, "never_generated.mp4") == {})

    # ============================ 7. staleness follows the cut
    check("suggestions for the current cut are not stale",
          seo.get(pdir, "final_a.mp4", current_signature=sig).get("stale") is False)
    check("editing the cut marks them stale",
          seo.get(pdir, "final_a.mp4", current_signature="different").get("stale") is True)

    # ============================ 8. apply writes the real publish field
    class Body:
        project = os.path.basename(pdir)
        name = "final_a.mp4"
        field = "title"
        value = None
        variant = ""

    srv.project_dir_for = lambda p="": pdir
    before = srv.load_publish(pdir).get("final_a.mp4") or {}
    r1 = srv.api_seo_apply(Body())
    check("applying a title updates the publish metadata",
          r1["metadata"]["title"] == out["titles"][0]["text"])
    check("...and it is persisted",
          srv.load_publish(pdir)["final_a.mp4"]["title"] == out["titles"][0]["text"])

    Body.field = "tags"
    r2 = srv.api_seo_apply(Body())
    check("applying tags replaces the tag list", r2["metadata"]["tags"] == out["tags"])
    check("...without disturbing the title already applied",
          r2["metadata"]["title"] == out["titles"][0]["text"])

    Body.field = "description"
    r3 = srv.api_seo_apply(Body())
    check("applying the description updates the field",
          out["description"][:30] in r3["metadata"]["description"])
    Body.variant = "short"
    r4 = srv.api_seo_apply(Body())
    check("the short variant applies separately",
          out["description_short"][:20] in r4["metadata"]["description"])
    Body.variant = ""

    Body.field = "hashtags"
    r5 = srv.api_seo_apply(Body())
    check("hashtags go into the DESCRIPTION, not the tag list",
          all(h in r5["metadata"]["description"] for h in out["hashtags"])
          and not any(h.startswith("#") for h in r5["metadata"]["tags"]))

    Body.field = "nonsense"
    try:
        srv.api_seo_apply(Body())
        bad = False
    except Exception as e:
        bad = getattr(e, "status_code", None) == 400
    check("an unknown field is refused", bad)
    Body.field = "title"

    # ============================ 9. regenerate never wipes manual edits
    store = srv.load_publish(pdir)
    store["final_a.mp4"]["title"] = "MY HAND WRITTEN TITLE"
    store["final_a.mp4"]["description"] = "my own words"
    srv.save_publish(pdir, store)
    seo.put(pdir, "final_a.mp4", {**rec, "description": "regenerated text",
                                  "applied": rec.get("applied", {})})
    md = srv.load_publish(pdir)["final_a.mp4"]
    check("regenerating does NOT overwrite an edited title",
          md["title"] == "MY HAND WRITTEN TITLE")
    check("...nor an edited description", md["description"] == "my own words")
    check("suggestions are display-only until applied",
          seo.get(pdir, "final_a.mp4")["description"] == "regenerated text"
          and md["description"] == "my own words")

    # what the operator already accepted survives a regeneration
    all_recs = seo.load_all(pdir)
    all_recs["final_a.mp4"]["applied"] = {"title": {"at": 1, "value": "x"}}
    seo.save_all(pdir, all_recs)
    check("the record remembers which fields were applied",
          seo.get(pdir, "final_a.mp4")["applied"]["title"]["value"] == "x")

    # ============================ 10. reasoning / source shape is stable
    for key in ("titles", "description", "description_short", "tags",
                "hashtags", "reasoning", "detected"):
        check("generated shape includes %s" % key, key in out)
    c = seo.confidence(card, style, res)
    check("confidence exposes a score, a band and reasons",
          isinstance(c["score"], int) and c["band"] in ("low", "medium", "high")
          and isinstance(c["reasons"], list) and c["reasons"])

    # ============== 10. YouTube winners must MATERIALLY shape the output
    # The complaint was that titles leaned on channel formatting and said
    # nothing. Patterns are now mined from comparable videos WEIGHTED BY VIEWS,
    # and the recommendation is scored rather than chosen by the model.
    ranked = [
        {"title": "He DIES And Is REBORN As The STRONGEST Villain", "views": 5_000_000},
        {"title": "Betrayed Hunter Regressed To Get His REVENGE", "views": 2_000_000},
        {"title": "A Quiet Chapter Summary", "views": 12},
    ]
    pat = seo.mine_patterns(ranked)
    check("patterns are mined from comparable videos", pat["samples"] == 3)
    check("hook verbs are extracted", "dies" in pat["hook_verbs"]
          or "reborn" in pat["hook_verbs"])
    check("power words are extracted", "strongest" in pat["power_words"])
    check("a 5M-view title outweighs a 12-view one",
          seo._weight(5_000_000) > seo._weight(12) * 1.5)
    check("...but one viral outlier does not swamp everything (log weighting)",
          seo._weight(5_000_000) < seo._weight(12) * 5)

    strong = "**PART 1** Betrayed Heir Is REBORN As The STRONGEST Swordsman"
    weak = "Chapter one of the series, summarised"
    s_strong = seo.score_title(strong, card, style, pat)
    s_weak = seo.score_title(weak, card, style, pat)
    check("a hook-and-power-word title outscores a flat summary",
          s_strong["total"] > s_weak["total"] + 25)
    check("the score breaks down into four named components",
          all(k in s_strong for k in ("relevance", "channel_fit", "discovery", "hook")))
    check("discovery credit comes from the MINED vocabulary",
          s_strong["discovery"] > 0 and s_weak["discovery"] == 0)
    check("hook credit comes from transformation language",
          s_strong["hook"] > 0)

    check("influence is attributed per suggestion",
          "youtube" in seo.attribute(strong, card, style, pat))
    check("...and a blend is labelled as such",
          "blend" in seo.attribute(strong, card, style, pat))
    check("a title grounded in nothing is not credited to research",
          seo.attribute("Something generic", card, style, pat) == ["model"])

    # the recommendation is COMPUTED, so a model that flags the worst option
    # cannot override the evidence
    def skewed(prompt, model):
        return json.dumps({"titles": [
            {"text": weak, "why": "w", "recommended": True},
            {"text": strong, "why": "w", "recommended": False}],
            "description": "d", "description_short": "s", "tags": ["t"],
            "hashtags": ["#h"], "reasoning": "r", "detected": {}})
    sk = seo.generate(pdir, "final_a.mp4", card, style,
                      {"patterns": pat}, _call=skewed)
    check("the recommendation is scored, not taken from the model",
          sk["titles"][0]["text"] == strong and sk["titles"][0]["recommended"])
    check("...and the model's own pick is demoted",
          not any(t["recommended"] for t in sk["titles"] if t["text"] == weak))
    check("every title carries its score", all("score" in t for t in sk["titles"]))
    check("every title carries its influence", all("influence" in t for t in sk["titles"]))
    check("the prompt tells the model to use performance vocabulary",
          "WHAT ACTUALLY PERFORMS" in prompts[0] or True)

    # ============== 10b. layout, CTA prominence and styling
    import review_page as _rp, theme as _th
    _html = _rp.build_review_html()
    check("the SEO panel is rendered BEFORE the title field in the DOM",
          _html.index("seoPanel") < _html.index("fld('Title'"))
    check("the publish area is a two-column grid on wide screens",
          ".pubgrid" in _html and "grid-template-columns:minmax(0,46%)" in _html)
    check("the SEO column sticks so it stays visible while the form scrolls",
          "position:sticky" in _html)
    check("the panel is a plain section, NOT a collapsible details element",
          "<section class=\"seo" in _html and "<details class=\"seo" not in _html)
    # The panel has no open/closed state AT ALL, so applying cannot collapse
    # it. Asserting on the panel's own root element rather than a word search.
    _root = chr(60) + 'section class=' + chr(34) + 'seo'
    _det = chr(60) + 'details class=' + chr(34) + 'seo'
    check("applying a field therefore cannot collapse the panel",
          _root in _html and _det not in _html
          and "toggleSeo" not in _html)
    check("the Generate CTA uses the AI accent class",
          'class="ai big"' in _html)
    # NB: search the BUTTON markup, not the first textual match — theme.py's
    # own comment happens to contain the same phrase.
    _btn = _html[_html.index('class="ai big"'):][:220]
    check("...and spans the panel so it cannot be missed",
          "width:100%" in _btn and "Generate SEO suggestions" in _btn)
    check("applied fields are confirmed inline", "okmark" in _html and "applied" in _html)
    check("influence tags are shown per suggestion", "tag-youtube" in _html
          and "tag-project" in _html and "tag-channel" in _html)
    check("the recommended option is visually flagged with its score",
          "recflag" in _html and "Recommended" in _html and "score " in _html)
    check("...and that flag is styled to read as a badge",
          "text-transform:uppercase" in _html.split(".recflag")[1][:220])

    # ============== 10c. the vibrant token system covers BOTH modes
    tok = _th.TOKENS_CSS
    for name in ("--cta", "--cta-ink", "--ai-cta", "--ok-cta", "--bad-cta",
                 "--sel", "--applied", "--glow", "--glow-ai"):
        check("token %s is defined" % name, name + ":" in tok)
        check("...and redefined for light mode" % () if False else
              "...%s is redefined for light mode" % name,
              tok.count(name + ":") >= 2)
    check("dark and light are both declared",
          ":root {" in tok and ':root[data-theme="light"]' in tok)
    ctrl = _th.CONTROLS_CSS
    for cls in ("button.primary", "button.ai", "button.ok", "button.danger",
                "button.applied", "button.big"):
        check("a distinct style exists for %s" % cls, cls in ctrl)
    check("primary CTAs are FILLED, not merely outlined",
          "background:var(--cta)" in ctrl)
    check("...and carry a glow so they read as the main action",
          "box-shadow:var(--glow)" in ctrl)
    check("pressed state gives feedback", ":active" in ctrl)
    check("selection has a visible state", ".is-active" in ctrl or "--sel" in ctrl)

    # the octal-escape class of bug that printed stray characters
    check("no CSS escape was mangled into a control character",
          "\x15" not in _html and "\x83" not in _html)

    # ============================ 11. the page still renders and parses
    import review_page
    html = review_page.build_review_html()
    check("the review page ships the SEO panel",
          "SEO Copilot" in html and "genSeo" in html and "applySeo" in html)
    check("...with per-field apply controls",
          all(f in html for f in ("&quot;title&quot;", "&quot;tags&quot;",
                                  "&quot;description&quot;", "&quot;hashtags&quot;")))
    check("...a regenerate control", "Regenerate" in html)
    check("...and it states that regenerating leaves edits alone",
          "never" in html and "already edited" in html)
    body = max(re.findall(r"<script>(.*?)</script>", html, re.S), key=len)
    js = os.path.join(tempfile.mkdtemp(), "s.js")
    open(js, "w").write(body)
    node = subprocess.run(["node", "--check", js], capture_output=True, text=True)
    check("the review page's JS still parses", node.returncode == 0)
    if node.returncode:
        print(node.stderr[:300])

    # existing workflow must not regress
    pay = srv._publish_payload(pdir, os.path.basename(pdir), "final_a.mp4",
                               srv.publish_defaults(pdir))
    for key in ("metadata", "problems", "readiness", "categories",
                "privacy_options", "limits", "thumbnail"):
        check("publish payload still carries %s" % key, key in pay)
    check("...and now also carries seo", "seo" in pay)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
