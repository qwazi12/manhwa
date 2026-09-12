"""SEO Copilot — YouTube metadata suggestions grounded in the actual recap.

Three stages, deliberately separated so the trustworthy part does not depend on
a language model:

  1. truth_card(pdir)      LOCAL and DETERMINISTIC. What this video actually is,
                           read from the ingested chapter URL, project.json,
                           the narration script, panel OCR and descriptions.
                           No network, no model — so it is fully unit-testable
                           and can never hallucinate an identity.
  2. channel_style(...)    MEASURED from the owner's own uploads via the
                           YouTube Data API. Patterns are counted, not asserted.
  3. generate(...)         ONE Gemini call that packages (1) in the manner of
                           (2), refined by bounded competitor research.

The order matters and is the whole point: project truth decides WHAT the video
is about; channel style and research only decide HOW it is packaged. Research
never gets to rename the manhwa.
"""
import json
import os
import re
import time
import collections

SEO_NAME = "seo.json"
CHANNEL_HANDLE = "@FlamingoRemix"
STYLE_CACHE = "_channel_style.json"
STYLE_TTL = 24 * 3600          # the channel changes slowly; one fetch a day

YT_TITLE_MAX = 100
YT_DESC_MAX = 5000
YT_TAGS_CHARS_MAX = 500

# Words that carry no discovery value and swamp any frequency count.
_STOP = set("""a an the and or but if then than that this these those of to in on at by for
with from as is are was were be been being it its it's he she they them his her their you your
we our i me my not no so up out over under again once here there when where why how all any both
each few more most other some such only own same too very can will just don should now into about
what which who whom while after before above below between through during off down""".split())


# ------------------------------------------------------------------ helpers
def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _words(text):
    return re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", text or "")


def _title_case_ratio(s):
    w = [x for x in (s or "").split() if x[:1].isalpha()]
    if not w:
        return 0.0
    return sum(1 for x in w if x[:1].isupper()) / len(w)


# =========================================================== 1. TRUTH CARD
def truth_card(pdir):
    """What this recap IS, from project data alone. No network, no model.

    Everything here is evidence the pipeline already produced, so a missing
    field means missing data — never a guess. `gaps` names what was absent so
    confidence can be lowered honestly instead of the model filling it in.
    """
    meta = _read(os.path.join(pdir, "project.json"), {}) or {}
    segs = _read(os.path.join(pdir, "segments.json"), []) or []
    descs = _read(os.path.join(pdir, "descriptions.json"), []) or []
    if isinstance(descs, dict):
        descs = list(descs.values())

    series = (meta.get("series") or "").strip()
    chapter = str(meta.get("chapter") or "").strip()
    source_url = (meta.get("url") or "").strip()

    # Narration, in order, is the closest thing to a plot summary that exists.
    beats = []
    for s in segs:
        for b in (s.get("beats") or []):
            t = (b.get("text") or "").strip()
            if t and (not beats or beats[-1] != t):
                beats.append(t)
    narration = " ".join(beats)

    ocr = " ".join((d.get("ocr_text") or "") for d in descs)
    visual = " ".join((d.get("visual_description") or "") for d in descs)

    # Character names: capitalised tokens that recur in the NARRATION (the
    # script names people; OCR is full of scanlation credits and sound effects).
    # Only count a capitalised word when it appears MID-SENTENCE at least once.
    # Sentence-initial capitals are just grammar: on real data this offered
    # "Yet" and "Shadows" as characters alongside Jin and Runcandel. A genuine
    # name turns up inside a sentence ("...mocked Jin..."); a stray capital
    # almost never does.
    midsentence = collections.Counter()
    for m in re.finditer(r"(^|[^.!?\"\u201c]\s+)([A-Z][a-z]{2,})\b", narration):
        w = m.group(2)
        if w.lower() not in _STOP:
            midsentence[w] += 1
    caps = collections.Counter(
        {w: c for w, c in midsentence.items() if c >= 1})
    aliases = _aliases(series, source_url, ocr)
    skip = {a.lower() for a in aliases} | {w.lower() for w in series.split()}
    # Threshold 2, not 3: it now counts ONLY mid-sentence appearances, which is
    # a much stricter measure than raw capitalised occurrences, so the old
    # cutoff would have found no characters at all in a short recap.
    characters = [w for w, c in caps.most_common(12) if c >= 2
                  and w.lower() not in skip][:6]

    # Genre from vocabulary actually present, so it is evidence not vibes.
    genre = _genre(narration, visual)

    # The hook: the opening narration is where the premise is established.
    hook = " ".join(beats[:3])[:400]

    terms = collections.Counter(
        w.lower() for w in _words(narration)
        if w.lower() not in _STOP and len(w) > 3)
    key_terms = [w for w, c in terms.most_common(25) if c >= 2][:15]

    gaps = []
    if not source_url:
        gaps.append("no ingested chapter URL on the project")
    if not series:
        gaps.append("no series name parsed from the ingest")
    if not chapter:
        gaps.append("no chapter number parsed from the ingest")
    if len(narration) < 400:
        gaps.append("narration is very short (%d chars)" % len(narration))
    if not descs:
        gaps.append("no panel descriptions available")
    if not characters:
        gaps.append("no recurring character names found in the narration")

    return {
        "series": series,
        "aliases": aliases,
        "chapter": chapter,
        "source_url": source_url,
        "characters": characters,
        "genre": genre,
        "hook": hook,
        "events": beats[:24],
        "key_terms": key_terms,
        "n_segments": len(segs),
        "n_panels": len(descs),
        "duration_sec": round(float(meta.get("duration") or 0), 1),
        "narration_chars": len(narration),
        "ocr_sample": ocr[:600],
        "gaps": gaps,
    }


def _aliases(series, url, ocr):
    """Alternate names, from the URL slug and the OCR title block only.

    Deliberately conservative: an alias invented here becomes a tag claiming
    the video is a different manhwa, so only forms actually observed count.
    """
    out = []
    if url:
        m = re.search(r"/comics/([a-z0-9\-]+?)(?:-[0-9a-f]{4,})?/?(?:chapter|$)", url)
        if m:
            slug = m.group(1).replace("-", " ").strip()
            if slug and slug.lower() != (series or "").lower():
                out.append(slug.title())
    if series:
        # "Swordmasters Youngest Son" is also written with apostrophes.
        apos = re.sub(r"\b(\w+)s\b", r"\1's", series)
        if apos != series:
            out.append(apos)
    seen, uniq = set(), []
    for a in out:
        if a.lower() not in seen and a.lower() != (series or "").lower():
            seen.add(a.lower())
            uniq.append(a)
    return uniq[:4]


_GENRE_WORDS = {
    "regression/reincarnation": ["regress", "reincarnat", "second life", "another life",
                                 "returned to the past", "past life"],
    "system/leveling": ["system", "level up", "levelled", "leveled", "skill", "status window",
                        "quest", "rank", "s-class", "ss-class"],
    "murim/martial arts": ["murim", "martial", "sect", "swordsman", "qi", "cultivat", "clan"],
    "dungeon/hunter": ["dungeon", "gate", "hunter", "raid", "guild", "monster"],
    "revenge": ["revenge", "vengeance", "betray", "retribution"],
    "academy": ["academy", "cadet", "school", "student"],
    "villain/antagonist": ["villain", "antagonist", "demon lord", "overlord"],
    "romance/drama": ["marriage", "love", "romance", "duchess", "empress", "prince"],
    "medical": ["doctor", "surgery", "clinic", "patient", "hospital", "medic"],
}


def _genre(narration, visual=""):
    """Genre from vocabulary actually present, narration weighted above art.

    The narration IS the plot, so it decides. Panel descriptions only support:
    a phrase repeated in boilerplate across a hundred panels would otherwise
    outvote the story ("clan banners on the wall" beating an actual regression).
    """
    nlow, vlow = (narration or "").lower(), (visual or "").lower()
    hits = []
    for g, ws in _GENRE_WORDS.items():
        n = sum(nlow.count(w) for w in ws) * 3 + min(sum(vlow.count(w) for w in ws), 6)
        if n >= 3:
            hits.append((g, n))
    hits.sort(key=lambda x: -x[1])
    return [g for g, _ in hits[:3]]


# ======================================================== 2. CHANNEL STYLE
def analyze_titles(titles):
    """Measure how this channel writes titles. Counted, never asserted.

    This exists because the obvious assumption was wrong: on Flamingo Remix the
    series name appears in about 4% of titles. The pattern is a premise hook
    ("Poor Gamer Unlocks SS-Class System To Become AN OVERPOWERED BLACKSMITH"),
    not "<Series> Chapter N Recap". Hardcoding the assumption would have
    produced titles that look nothing like the channel, so it is derived.
    """
    titles = [t for t in (titles or []) if t]
    if not titles:
        return {"samples": 0}
    marker = re.compile(r'^\s*(\*\*[^*]+\*\*|\*[^*]+\*|\([^)]*\)|\[[^\]]*\])')
    n_marker = sum(1 for t in titles if marker.match(t))
    n_caps = sum(1 for t in titles if re.search(r"\b[A-Z]{3,}\b", t))
    n_quote = sum(1 for t in titles if '"' in t or "“" in t)
    n_part = sum(1 for t in titles
                 if re.search(r"\b(part|pt|ep|episode|chapter)\b\s*\d", t, re.I))
    lens = [len(t) for t in titles]
    examples = [t for t in titles if marker.match(t)][:6] or titles[:6]
    return {
        "samples": len(titles),
        "avg_length": round(sum(lens) / len(lens)),
        "min_length": min(lens), "max_length": max(lens),
        "pct_leading_marker": round(100 * n_marker / len(titles)),
        "pct_allcaps_emphasis": round(100 * n_caps / len(titles)),
        "pct_quoted_series": round(100 * n_quote / len(titles)),
        "pct_part_number": round(100 * n_part / len(titles)),
        "avg_title_case": round(sum(_title_case_ratio(t) for t in titles) / len(titles), 2),
        "examples": examples,
    }


def analyze_descriptions(descs):
    """Structure of the channel's descriptions, plus its real hashtag set."""
    descs = [d for d in (descs or []) if d and d.strip()]
    if not descs:
        return {"samples": 0, "top_hashtags": []}
    tags = collections.Counter()
    for d in descs:
        for h in re.findall(r"#\w+", d):
            tags[h.lower()] += 1
    blocks = collections.Counter()
    for d in descs:
        low = d.lower()
        for key, pat in (("timecodes", "timecode"), ("credits", "credit"),
                         ("support/kofi", "ko-fi"), ("music", "background music"),
                         ("comment_cta", "drop a comment")):
            if pat in low:
                blocks[key] += 1
    return {
        "samples": len(descs),
        "avg_length": round(sum(len(d) for d in descs) / len(descs)),
        "top_hashtags": [h for h, _ in tags.most_common(14)],
        "common_blocks": {k: round(100 * v / len(descs)) for k, v in blocks.items()},
        "example": descs[0][:900],
    }


def channel_style(client, cache_dir, handle=CHANNEL_HANDLE, force=False, limit=50):
    """Fetch and measure the channel's style, cached for a day.

    Cached because it costs quota and barely changes, and because a generation
    should not fail just because YouTube is briefly unavailable — a stale style
    card is far better than none.
    """
    path = os.path.join(cache_dir, STYLE_CACHE)
    cached = _read(path)
    if cached and not force and (time.time() - cached.get("fetched_at", 0)) < STYLE_TTL:
        cached["from_cache"] = True
        return cached
    try:
        ch = client.channel(handle)
        ups = client.uploads(ch["uploads_playlist"], limit=limit) if ch.get("uploads_playlist") else []
        style = {
            "handle": handle,
            "channel_title": ch.get("title"),
            "channel_id": ch.get("channel_id"),
            "video_count": ch.get("video_count"),
            "subscriber_count": ch.get("subscriber_count"),
            "titles": analyze_titles([u["title"] for u in ups]),
            "descriptions": analyze_descriptions([u["description"] for u in ups]),
            "fetched_at": time.time(),
            "error": None,
        }
        try:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(style, f, indent=1)
            os.replace(tmp, path)
        except OSError:
            pass
        return style
    except Exception as e:
        if cached:                       # stale beats nothing
            cached["from_cache"] = True
            cached["stale_reason"] = str(e)[:160]
            return cached
        return {"handle": handle, "titles": {"samples": 0},
                "descriptions": {"samples": 0, "top_hashtags": []},
                "error": str(e)[:200], "fetched_at": time.time()}


# ============================================================ 3. RESEARCH
def research(client, card, max_results=10):
    """ONE bounded search for comparable recaps, ranked by actual views.

    Deliberately a single search.list (100 quota units) plus one videos.list
    (1 unit). Per-keyword searching would burn the daily quota in a handful of
    generations for marginal extra signal.

    Returns STRUCTURAL patterns and the query used — not a pile of competitor
    titles to imitate. Verbatim copying is prevented at the source: the
    generator is given aggregate shape (length, caps usage, hook phrasing
    counts), and titles only as clearly-labelled context it is told never to
    reuse.
    """
    genre = (card.get("genre") or [""])[0].split("/")[0]
    q = " ".join(x for x in ["manhwa recap", genre] if x).strip() or "manhwa recap"
    try:
        hits = client.search_recaps(q, limit=max_results)
    except Exception as e:
        return {"query": q, "ok": False, "error": str(e)[:200],
                "n": 0, "patterns": {}, "top": []}
    stats = {}
    try:
        stats = client.video_stats([h["video_id"] for h in hits])
    except Exception:
        pass
    for h in hits:
        s = stats.get(h.get("video_id") or "", {})
        h["views"] = s.get("views", 0)
    ranked = sorted(hits, key=lambda h: -h.get("views", 0))
    titles = [h["title"] for h in ranked]
    # Phrases that recur across competitors — the discovery vocabulary, which
    # is the part worth learning. Individual titles are not.
    grams = collections.Counter()
    for t in titles:
        w = [x.lower() for x in _words(t)]
        for n in (2, 3):
            for i in range(len(w) - n + 1):
                g = " ".join(w[i:i + n])
                if not all(x in _STOP for x in w[i:i + n]):
                    grams[g] += 1
    return {
        "query": q,
        "ok": True,
        "n": len(ranked),
        "patterns": {**analyze_titles(titles),
                     **mine_patterns(ranked),
                     "common_phrases": [g for g, c in grams.most_common(12) if c >= 2]},
        "top": [{"title": h["title"], "channel": h["channel"],
                 "views": h.get("views", 0),
                 "url": "https://www.youtube.com/watch?v=" + (h.get("video_id") or "")}
                for h in ranked[:6]],
    }


# =================================================== 3b. WINNER PATTERNS
# The vocabulary that actually drives recap discovery. Mined FROM results
# rather than hardcoded as taste — the lexicon below only decides which
# matches get counted as "hook language", not what the copy says.
HOOK_VERBS = [
    "dies", "died", "killed", "reborn", "reincarnat", "regress", "returns",
    "returned", "betray", "becomes", "became", "awakens", "awakened", "unlocks",
    "gains", "transform", "rises", "hides", "reveals", "destroys", "defeats",
    "survives", "escapes", "inherits", "summoned", "trapped", "abandoned",
    "bullied", "mocked", "underestimated", "humiliated", "sacrific",
]
POWER_WORDS = [
    "strongest", "ultimate", "overpowered", "op", "hidden", "secret", "legendary",
    "supreme", "greatest", "useless", "weakest", "genius", "god", "max", "sss",
    "ss-class", "s-class", "cheat", "system", "level", "rank", "forbidden",
    "ancient", "immortal", "apocalypse", "villain", "revenge",
]


def _weight(views):
    """A 16M-view video should inform packaging more than a 1k one, but not
    1,600x more — log keeps one viral outlier from dictating everything."""
    import math
    return 1.0 + math.log10(max(views, 1) + 1)


def mine_patterns(ranked):
    """Packaging signals from comparable videos, weighted by real views.

    Returns COUNTS and RANKED VOCABULARY, never titles to reuse. The generator
    is given this instead of the raw list precisely so it cannot copy: there is
    nothing here to copy from.
    """
    ranked = [r for r in (ranked or []) if r.get("title")]
    if not ranked:
        return {"samples": 0}
    hooks, powers, openers, grams = (collections.Counter() for _ in range(4))
    lens, caps, nums = [], 0, 0
    for r in ranked:
        t = r["title"]
        w = _weight(r.get("views", 0))
        low = t.lower()
        lens.append(len(t))
        if re.search(r"\b[A-Z]{3,}\b", t):
            caps += 1
        if re.search(r"\b(part|pt|ep|episode|chapter)\s*\d", t, re.I):
            nums += 1
        for v in HOOK_VERBS:
            if v in low:
                hooks[v] += w
        for v in POWER_WORDS:
            if re.search(r"\b" + re.escape(v), low):
                powers[v] += w
        first = _words(t)[:3]
        if first:
            openers[" ".join(x.lower() for x in first[:2])] += w
        toks = [x.lower() for x in _words(t)]
        for n in (2, 3):
            for i in range(len(toks) - n + 1):
                g = " ".join(toks[i:i + n])
                if not all(x in _STOP for x in toks[i:i + n]):
                    grams[g] += w
    n = len(ranked)
    return {
        "samples": n,
        "avg_length": round(sum(lens) / n),
        "pct_allcaps": round(100 * caps / n),
        "pct_part_number": round(100 * nums / n),
        # ranked by view-weighted frequency, so these ARE the winning words
        "hook_verbs": [h for h, _ in hooks.most_common(10)],
        "power_words": [p for p, _ in powers.most_common(12)],
        "opening_patterns": [o for o, _ in openers.most_common(6)],
        "discovery_phrases": [g for g, c in grams.most_common(18) if c > 1.5][:14],
        "median_views": sorted(r.get("views", 0) for r in ranked)[n // 2],
    }


def score_title(text, card, style, patterns):
    """Rank a candidate on the four things that actually decide a recap title.

    Exposed as components rather than one number so the panel can SHOW why a
    title was recommended instead of asserting it.
    """
    t = (text or "").strip()
    low = t.lower()
    if not t:
        return {"total": 0}

    # 1. Project relevance — is this about OUR video, not a generic recap?
    terms = [w for w in ((card.get("characters") or []) +
                         (card.get("key_terms") or [])) if len(w) > 3]
    hit = sum(1 for w in terms if w.lower() in low)
    relevance = min(30, hit * 10)
    if (card.get("series") or "").lower() in low:
        relevance = min(30, relevance + 6)

    # 2. Channel fit — measured, not assumed
    st = (style or {}).get("titles", {}) or {}
    fit = 0
    if st.get("samples"):
        if re.match(r"^\s*(\*\*[^*]+\*\*|\*[^*]+\*|\([^)]*\))", t):
            fit += 10 if st.get("pct_leading_marker", 0) >= 50 else 2
        if re.search(r"\b[A-Z]{3,}\b", t):
            fit += 8 if st.get("pct_allcaps_emphasis", 0) >= 50 else 2
        target = st.get("avg_length") or 70
        fit += max(0, 7 - abs(len(t) - target) // 8)
    else:
        fit = 8                                   # no signal: neutral, not zero
    fit = min(25, fit)

    # 3. Discovery power — vocabulary that performs in the niche
    pw = patterns or {}
    disc = 0
    for p in (pw.get("power_words") or [])[:12]:
        if re.search(r"\b" + re.escape(p), low):
            disc += 4
    for g in (pw.get("discovery_phrases") or [])[:14]:
        if g in low:
            disc += 3
    discovery = min(25, disc)

    # 4. Hook strength — transformation/stakes language
    hk = sum(4 for v in (pw.get("hook_verbs") or HOOK_VERBS)[:10] if v in low)
    if re.search(r"\b(but|until|after|when|because)\b", low):
        hk += 3                                   # a turn implies a story
    hook = min(20, hk)

    total = relevance + fit + discovery + hook
    return {"total": int(total), "relevance": int(relevance), "channel_fit": int(fit),
            "discovery": int(discovery), "hook": int(hook)}


def attribute(text, card, style, patterns):
    """Where a suggestion's language came from — shown to the operator."""
    low = (text or "").lower()
    src = []
    terms = [w for w in ((card.get("characters") or []) +
                         (card.get("key_terms") or [])) if len(w) > 3]
    if any(w.lower() in low for w in terms) or (card.get("series") or "").lower() in low:
        src.append("project")
    st = (style or {}).get("titles", {}) or {}
    if st.get("samples") and (
            re.match(r"^\s*(\*\*|\*|\()", text or "") or
            re.search(r"\b[A-Z]{3,}\b", text or "")):
        src.append("channel")
    pw = patterns or {}
    if any(re.search(r"\b" + re.escape(p), low) for p in (pw.get("power_words") or [])[:12]) \
       or any(g in low for g in (pw.get("discovery_phrases") or [])[:14]) \
       or any(v in low for v in (pw.get("hook_verbs") or [])[:10]):
        src.append("youtube")
    if len(src) >= 2:
        src.append("blend")
    return src or ["model"]


# ========================================================== 4. CONFIDENCE
def confidence(card, style, res):
    """Computed from real signals, never asserted by the model.

    Weighted towards PROJECT truth, because knowing what the video actually is
    matters more to metadata quality than knowing how rivals package theirs.
    """
    pts, why = 0, []
    if card.get("source_url"):
        pts += 20; why.append("ingested chapter URL present")
    else:
        why.append("no ingested URL (-20)")
    if card.get("series"):
        pts += 20; why.append("series name parsed")
    else:
        why.append("series name missing (-20)")
    if card.get("chapter"):
        pts += 5
    n = card.get("narration_chars", 0)
    if n >= 3000:
        pts += 20; why.append("full narration available")
    elif n >= 800:
        pts += 12; why.append("partial narration")
    else:
        why.append("narration very short (-20)")
    if card.get("characters"):
        pts += 10; why.append("%d recurring characters" % len(card["characters"]))
    if card.get("genre"):
        pts += 5
    s = (style or {}).get("titles", {}).get("samples", 0)
    if s >= 20:
        pts += 15; why.append("channel style from %d uploads" % s)
    elif s >= 5:
        pts += 8; why.append("channel style from only %d uploads" % s)
    else:
        why.append("no channel style signal (-15)")
    if (res or {}).get("ok") and res.get("n"):
        pts += 5; why.append("%d comparable videos researched" % res["n"])
    else:
        why.append("no competitor research (-5)")
    pts = max(0, min(100, pts))
    band = "high" if pts >= 75 else ("medium" if pts >= 50 else "low")
    return {"score": pts, "band": band, "reasons": why}


# ========================================================= 5. PERSISTENCE
def load_all(pdir):
    return _read(os.path.join(pdir, SEO_NAME), {}) or {}


def save_all(pdir, data):
    tmp = os.path.join(pdir, SEO_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, os.path.join(pdir, SEO_NAME))


def get(pdir, name, current_signature=None):
    """One export's SEO record, with staleness derived at READ time.

    Derived rather than stored, exactly like the review verdict's `superseded`
    flag: a stored flag goes out of date the moment the timeline is edited,
    whereas comparing signatures is always current.
    """
    rec = load_all(pdir).get(os.path.basename(name or "")) or {}
    if rec and current_signature:
        rec = dict(rec)
        rec["stale"] = bool(rec.get("cut_signature")
                            and rec["cut_signature"] != current_signature)
    return rec


def put(pdir, name, rec):
    data = load_all(pdir)
    data[os.path.basename(name or "")] = rec
    save_all(pdir, data)
    return rec


# ========================================================== 6. GENERATION
PROMPT = """You write YouTube metadata for a manhwa recap channel.

THE VIDEO (ground truth from the publisher's own pipeline — this is what the
video actually contains; never contradict it, never rename the series):
{truth}

THE CHANNEL'S MEASURED STYLE (counted from this channel's real uploads — match
this voice, it is the house style):
{style}

WHAT ACTUALLY PERFORMS IN THIS NICHE (mined from comparable recaps and
weighted by real view counts — this is the discovery evidence, use it hard):
{research}

RULES
- Ground every claim in THE VIDEO. If something is not in the narration or
  panel data, do not state it. Never invent characters, plot points, or a
  different series.
- Match the channel's measured title SHAPE (part marker, ALLCAPS emphasis,
  length). If the channel rarely names the series in the title, you should not
  either — the series name belongs in tags and the description.
- But the WORDS should come from what performs. Build each title around the
  hook verbs and power words listed under WHAT ACTUALLY PERFORMS: a concrete
  transformation or stake (dies / regressed / betrayed / becomes / unlocks /
  underestimated), not a neutral summary. Channel style is the format;
  performance vocabulary is the content. Vary the angle across the options —
  do not submit four rewrites of one sentence.
- Tags and hashtags must likewise mix project terms, alias forms, AND the
  discovery phrases listed above — not a generic keyword list.
- Write ORIGINAL titles. Never reuse a competitor's title or a distinctive
  phrase from one. Learn the shape, not the words.
- Titles must be <= {tmax} characters.
- No clickbait that the recap does not deliver.
- Tags: total length under {tagmax} characters across all tags.
- Hashtags: 3-6, relevant, not stuffed.
- NEVER write a URL, a donation link, a social handle, or a timecode. You do
  not know the channel's real links and you cannot see where anything happens
  in the video, so any you produce would be wrong. The publisher adds those
  afterwards. Write only prose about the story.

Return ONLY a JSON object, no markdown fence:
{{
  "titles": [
    {{"text": "...", "why": "one short sentence", "recommended": true|false}}
  ],
  "description": "primary long description",
  "description_short": "shorter variant",
  "tags": ["..."],
  "hashtags": ["#..."],
  "reasoning": "3-5 sentences: what you took from the video, from the channel
                style, and from the research",
  "detected": {{"series": "...", "chapter": "...", "premise": "one sentence"}}
}}
Exactly one title must have "recommended": true. Provide 4 titles."""


def _compact(card):
    """The truth card, trimmed to what actually informs packaging."""
    return {
        "series": card.get("series"), "aliases": card.get("aliases"),
        "chapter": card.get("chapter"), "source_url": card.get("source_url"),
        "genre": card.get("genre"), "characters": card.get("characters"),
        "premise_hook": card.get("hook"),
        "key_events": card.get("events", [])[:14],
        "recurring_terms": card.get("key_terms"),
        "runtime_seconds": card.get("duration_sec"),
        "missing_data": card.get("gaps"),
    }


def _style_brief(style):
    t = (style or {}).get("titles", {}) or {}
    d = (style or {}).get("descriptions", {}) or {}
    if not t.get("samples"):
        return {"note": "No channel style signal available — use neutral, "
                        "descriptive packaging and say so in the reasoning."}
    return {
        "titles_measured_from": t.get("samples"),
        "avg_title_length": t.get("avg_length"),
        "pct_with_leading_part_marker": t.get("pct_leading_marker"),
        "pct_with_ALLCAPS_emphasis": t.get("pct_allcaps_emphasis"),
        "pct_naming_the_series_in_title": t.get("pct_quoted_series"),
        "pct_with_part_number": t.get("pct_part_number"),
        "the_channels_own_titles": t.get("examples", [])[:6],
        "description_avg_length": d.get("avg_length"),
        "channel_hashtags": d.get("top_hashtags", [])[:12],
        "description_blocks": d.get("common_blocks", {}),
    }


def _parse(text):
    """Pull the JSON object out of the reply, fence or no fence."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise ValueError("model did not return JSON")
        return json.loads(m.group(0))


_TIMECODE = re.compile(r"^\s*[\[(]?\d{1,2}:\d{2}(?::\d{2})?[\])]?\s*[-–—:.)]?\s*.*$")
_URL = re.compile(r"https?://\S+|www\.\S+")


def sanitize_description(text, card):
    """Strip anything the copilot cannot actually know.

    Measured against the real channel, the model copies its description
    furniture — and invents the contents: a Ko-fi link that was not the
    publisher's, and a full timecode list for a video it has never seen. A
    wrong donation link is worse than no link, and invented chapter markers
    send viewers to the wrong moment. Instructions alone did not stop it, so
    this removes them.

    The ONE URL allowed through is the ingested source chapter, because that
    is project truth rather than a guess.
    """
    allowed = (card or {}).get("source_url") or ""
    kept, dropped = [], []
    for line in (text or "").splitlines():
        if _TIMECODE.match(line) and re.search(r"\d{1,2}:\d{2}", line):
            dropped.append("timecode")
            continue
        urls = _URL.findall(line)
        bad = [u for u in urls if allowed and not u.startswith(allowed[:40])]
        bad += [u for u in urls if not allowed]
        if bad:
            line = _URL.sub("", line)
            dropped.append("url")
            if not line.strip(" -•·\t"):
                continue
        if re.search(r"pinned comment|link in (?:the )?(?:bio|description)",
                     line, re.I):
            dropped.append("unverifiable reference")
            continue
        kept.append(line)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    # An empty section header left behind by the stripping reads as a bug.
    out = re.sub(r"(?m)^\s*[\[\(]?\s*(TIMECODES?|CHAPTERS?|SUPPORT[^\]\)\n]*|MUSIC)\s*[\]\)]?\s*:?\s*$\n?",
                 "", out, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", out).strip(), sorted(set(dropped))


def _clamp(out, card, style=None, patterns=None):
    """Enforce the hard limits in code. A model that drifts past YouTube's
    caps would otherwise produce metadata the publish step silently rejects."""
    titles = []
    for t in (out.get("titles") or [])[:5]:
        txt = (t.get("text") or "").strip()[:YT_TITLE_MAX]
        if txt:
            titles.append({"text": txt, "why": (t.get("why") or "").strip(),
                           "recommended": bool(t.get("recommended"))})
    # The RECOMMENDATION is computed, not taken from the model. Its own pick
    # reflected the prompt's emphasis rather than measurable strength, which is
    # how the suggestions ended up sounding channel-shaped but generic.
    for t in titles:
        t["score"] = score_title(t["text"], card, style, patterns)
        t["influence"] = attribute(t["text"], card, style, patterns)
        t["recommended"] = False
    if titles:
        best = max(range(len(titles)), key=lambda i: titles[i]["score"]["total"])
        titles[best]["recommended"] = True
    titles.sort(key=lambda t: -t["score"]["total"])

    tags, total = [], 0
    for tag in (out.get("tags") or []):
        tag = re.sub(r"\s+", " ", str(tag)).strip().strip(",")
        if not tag or tag.lower() in {x.lower() for x in tags}:
            continue
        if total + len(tag) + 1 > YT_TAGS_CHARS_MAX:
            break
        tags.append(tag)
        total += len(tag) + 1

    hashtags, seen_h = [], set()
    for h in (out.get("hashtags") or [])[:8]:
        h = str(h).strip()
        h = h if h.startswith("#") else "#" + h.lstrip("#")
        h = re.sub(r"[^#\w]", "", h)
        if len(h) > 1 and h.lower() not in seen_h:
            seen_h.add(h.lower())
            hashtags.append(h)

    desc, dropped = sanitize_description(out.get("description"), card)
    short, dropped2 = sanitize_description(out.get("description_short"), card)
    det = out.get("detected") or {}
    return {
        "titles": titles,
        "description": desc[:YT_DESC_MAX],
        "description_short": short[:YT_DESC_MAX],
        "sanitized": sorted(set(dropped + dropped2)),
        "tags": tags,
        "hashtags": hashtags[:6],
        "reasoning": (out.get("reasoning") or "").strip(),
        # The model may only REPORT identity; the project is the authority, so
        # series/chapter are overwritten from the truth card, never taken.
        "detected": {"series": card.get("series") or det.get("series") or "",
                     "chapter": card.get("chapter") or str(det.get("chapter") or ""),
                     "premise": (det.get("premise") or "").strip()},
    }


def generate(pdir, name, card, style, res, model=None, _call=None, usage=None):
    """One gated model call that packages the truth card in the channel's voice."""
    model = model or os.environ.get("SEO_MODEL", "gemini-3.5-flash")
    prompt = PROMPT.format(
        truth=json.dumps(_compact(card), indent=1, ensure_ascii=False),
        style=json.dumps(_style_brief(style), indent=1, ensure_ascii=False),
        research=json.dumps((res or {}).get("patterns", {}), indent=1, ensure_ascii=False),
        tmax=YT_TITLE_MAX, tagmax=YT_TAGS_CHARS_MAX)

    if _call is not None:                  # tests drive the model
        raw = _call(prompt, model)
    else:
        from google import genai
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

        def do():
            r = client.models.generate_content(model=model, contents=prompt)
            return r
        if usage is not None:
            # Never bypass the cost guardrails, and report real tokens so the
            # ledger prices this correctly rather than at a flat per-call rate.
            with usage.gate("gemini", 1, model=model) as meter:
                resp = do()
                meter.from_response(resp)
        else:
            resp = do()
        raw = resp.text or ""
    return _clamp(_parse(raw), card, style, (res or {}).get("patterns"))
