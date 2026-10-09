"""Story research: build a Series Bible for every series, from sourced web research.

Why (owner, 2026-10-04): the Series Bible (series_bible.py) is what made Murim
v5 right — canonical names, pronouns, looks, the "lion-dance heads are props"
rule — because ingest feeds it to describe, narrate and SEO. Only Murim had
one, hand-written. Every other series' chapters were written without a cast.

How (borrowed from Scrapper's LongForm "Research — facts & sources"):
  1. Grounded research: Gemini + Google Search. Every finding is a span of the
     answer with the sources that support it.
  2. Sources are resolved (Google's redirect links) and graded:
       official  publisher / platform pages
       trusted   encyclopedias and databases
       wiki      fan wikis — owner rule: allowed for NAMES, PRONOUNS and LOOKS
                 only (the chapter's panels must agree; the image + OCR check
                 in describe is that agreement)
       low       forums, social, video — never a fact
  3. A second call (no tools) turns the research into the bible's schema,
     citing source numbers per entry. Characters need an official, trusted or
     wiki source; world/plot facts need official or trusted, otherwise they
     are kept but marked unverified and NOT fed to the narrator.
  4. Merge: anything already in the bible (hand-written or owner-edited)
     wins; research only ADDS characters and fills empty fields. Murim's
     hand-written bible stays the gold standard.

Cost: two Gemini 3.8 Flash calls per series (~$0.01–0.03; Google Search
grounding free for the first 5,000/month). Metered through usage.gate.
"""

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

MODEL = os.environ.get("RESEARCH_MODEL", "gemini-3.8-flash")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

OFFICIAL = ("webtoons.com", "naver.com", "kakao.com", "kakaopage.com", "kakaoent.com",
            "tapas.io", "tappytoon.com", "lezhin.com", "manta.net", "pocketcomics.com",
            "yenpress.com", "ridibooks.com", "asuracomic.net", "asurascans.com")
TRUSTED = ("wikipedia.org", "animenewsnetwork.com", "mangaupdates.com", "anilist.co",
           "myanimelist.net", "novelupdates.com", "anime-planet.com", "kitsu.app",
           "cbr.com", "screenrant.com", "polygon.com", "crunchyroll.com", "namu.wiki")
WIKI = ("fandom.com", "wikia.org", "wiki.gg")
LOW = ("reddit.com", "quora.com", "tiktok.com", "x.com", "twitter.com", "facebook.com",
       "instagram.com", "youtube.com", "pinterest.com", "medium.com", "blogspot.com",
       "wordpress.com", "tumblr.com", "discord.com", "discord.gg")
FACT_TIERS = ("official", "trusted")
# Names, pronouns and looks: fan wikis and unlisted sites ("other") are allowed
# (owner's fan-wiki rule; 2026-10-04 live run: Fog Land's 7 characters were
# cited only to unlisted sites and got dropped). Forums/social never count.
NAME_TIERS = ("official", "trusted", "wiki", "other")


# ------------------------------------------------------------------ sources
def domain(url):
    host = urllib.parse.urlparse(url).netloc if "://" in (url or "") else (url or "")
    host = host.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def tier(host):
    def m(names):
        return any(host == n or host.endswith("." + n) for n in names)
    if m(OFFICIAL):
        return "official"
    if m(TRUSTED):
        return "trusted"
    if m(WIKI):
        return "wiki"
    if m(LOW):
        return "low"
    return "other"


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def resolve(url, timeout=8):
    """Follow Google's grounding redirect to the real page (best effort)."""
    if "grounding-api-redirect" not in (url or ""):
        return url

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_ctx()))
    try:
        opener.open(urllib.request.Request(url, method="HEAD"), timeout=timeout)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        if loc:
            return loc
    except Exception:
        pass
    return url


# ------------------------------------------------------------------ gemini
def _post(body, api_key, _urlopen=None):
    import usage
    req = urllib.request.Request(URL.format(m=MODEL), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-goog-api-key": api_key}, method="POST")
    opener = _urlopen or urllib.request.urlopen
    with usage.gate("gemini", 1, model=MODEL) as m:
        with opener(req, timeout=180, context=_ctx()) as r:
            data = json.loads(r.read())
        u = usage.parse_gemini_usage(data)
        if u:
            m.tokens(u["prompt"], u["output"], u["cached"])
    return data


def _text(data):
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def research_prompt(title, aliases, series_url, latest, window):
    also = f" (also known as {', '.join(aliases)})" if aliases else ""
    return f"""Research the Korean manhwa / webtoon "{title}"{also}. We read it at {series_url}.
Its latest chapter there is {latest}. We are making recap videos of chapters {window}.

Report, as short factual sentences:
1. Premise and genre, as official sources describe it.
2. The main characters: full name, other names/titles/nicknames and romanization variants,
   gender and pronouns, role and affiliation, and distinctive appearance (hair, clothing,
   weapon, aura, eyes) — the things you would recognise them by in a panel.
3. Important relationships between them (allies, rivals, family, master/disciple).
4. Factions, sects, clans, guilds or organisations.
5. The power system and recurring terms (with alternative spellings).
6. What happens in the story around chapters {window} (the current arc), if sources say.
7. Anything sources disagree on, or names that are often confused — say which source says what.

Prefer official publisher/platform pages, Wikipedia, Anime News Network, MangaUpdates, AniList,
MyAnimeList, Novel Updates. Fan wikis are acceptable for character names, pronouns and appearance.
Do not use Reddit, forums, YouTube, TikTok or social media. Do not speculate; if something is
unknown, say it is unknown."""


def seo_research_prompt(title, aliases, series_url, latest):
    also = f" (also known as: {', '.join(aliases)})" if aliases else ""
    return f"""Research the comic "{title}"{also}. We read it at {series_url}. Latest chapter we know: {latest}.

GOAL: SEO metadata for a YouTube recap channel. Search the web for: the series' pages on
MangaUpdates, its official publisher page (Naver Webtoon / Kakao / KuaiKan / Webnovel / Asura),
its Fandom wiki, and YouTube videos recapping it.

RETURN, each finding as a span of text with the source URLs that support it:

1. CANONICAL ENGLISH TITLE and MEDIUM (Korean manhwa / Chinese manhua).
2. OFFICIAL ORIGINAL-LANGUAGE TITLE (the one on the publisher's own page).
3. ALL ASSOCIATED / ALTERNATE TITLES, every language, copied verbatim.
4. STORY credits and ART credits. If sources disagree (romanizations), report BOTH with their sources.
5. ORIGINAL PUBLISHER / PLATFORM.
6. GENRES AND TAGS as listed.
7. MAIN CHARACTERS (name + role only).
8. SEASON/ARC STRUCTURE and LATEST CHAPTER NUMBER with its release date — use release logs and
   dated entries ONLY; ignore undated "status" fields (they go stale).
9. THE OFFICIAL SYNOPSIS, verbatim, the one shown on the series page.
10. YOUTUBE RECON: list recap videos of this series you find — for each: exact title, channel,
    view count if shown, upload date, and whether the title is (a) bare series name,
    (b) chapter-number style like [1-45] or (44), or (c) a story hook ("He Thought X...").
    Note any video whose description contradicts its own title or contains template text
    from another channel.

RULES:
- Names, titles and alternate spellings may come from fan wikis. PLOT facts need an official or
  database source (mangaupdates.com, official publisher). Forums/social/video are NEVER a fact source.
- Never guess a missing field. Write "NOT FOUND" for anything you cannot source.
- Report conflicts; do not silently pick one."""


def seo_structure_prompt(title, research_text, numbered_sources):
    return f"""Turn this research about the comic "{title}" into JSON. Use ONLY facts stated in the
research. Cite the source number(s) supporting each entry.

{research_text}

Sources:
{numbered_sources}

Return ONLY JSON:
{{
 "series_name_en": "...",
 "medium": "manhwa|manhua",
 "series_name_original": {{"value": "...", "sources": [n]}},
 "series_name_alt": ["..."],
 "authors": {{"story": {{"value": ["..."], "sources": [n], "conflicts": ["..."]}},
              "art": {{"value": ["..."], "sources": [n], "conflicts": ["..."]}}}},
 "publisher": {{"value": "...", "sources": [n]}},
 "genres": ["..."],
 "genre_tag_phrases": ["2-3 tag phrases per genre, e.g. 'cultivation manhua'"],
 "characters_main": ["..."],
 "seasons": [{{"n": 1, "name": "...", "chapters": "1-34"}}],
 "latest_chapter": {{"number": n, "released_utc": "...", "sources": [n]}},
 "synopsis_verbatim": {{"value": "...", "sources": [n]}},
 "youtube_recon": [{{"title": "...", "channel": "...", "views": null, "date": "",
                    "style": "bare|bracket|hook", "weakness": ""}}],
 "gap": "one sentence: is exact-phrase + hook format unoccupied, and which mechanic fits"
}}

RULES: characters go ONLY in characters_main, never in series_name_alt. Never invent a value;
use null and cite nothing. genre_tag_phrases must be derived only from listed genres."""


def research_series_seo(series_id, pack=None, api_key=None, _urlopen=None, _resolve=None):
    """Spec-10 Phase B web research + structuring for SEO metadata.
    Two calls: Gemini + Google Search grounding, then JSON structuring with source citations.
    Metered through usage.gate."""
    if not api_key:
        try:
            import gemini_tts as _gt
            api_key = _gt.env_any_case("GEMINI_API_KEY")
        except Exception:
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("gemini_api_key")
    pack = pack or {}
    title = pack.get("series_name_en") or pack.get("title_en") or pack.get("canonical_title") or str(series_id)
    aliases = list(pack.get("aliases") or pack.get("aliases_manual") or [])
    series_url = pack.get("source_url") or pack.get("series_url") or ""
    latest = pack.get("latest_chapter") or pack.get("total_chapters") or ""
    if isinstance(latest, dict):
        latest = latest.get("number") or ""

    # Call 1: Grounded research call
    data = _post({"contents": [{"parts": [{"text": seo_research_prompt(title, aliases, series_url, latest)}]}],
                  "tools": [{"google_search": {}}],
                  "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192}}, api_key, _urlopen)
    text = _text(data)
    meta = ((data.get("candidates") or [{}])[0]).get("groundingMetadata") or {}
    res = _resolve or resolve
    sources = []
    for c in meta.get("groundingChunks", []):
        web = c.get("web") or {}
        url = res(web.get("uri", ""))
        host = domain(url) if "://" in url and "grounding-api-redirect" not in url else domain(web.get("title", ""))
        sources.append({"title": web.get("title", ""), "url": url, "domain": host, "tier": tier(host)})
    claims = [{"text": (s.get("segment") or {}).get("text", ""),
               "sources": s.get("groundingChunkIndices", [])} for s in meta.get("groundingSupports", [])]

    # Call 2: Structuring call (JSON, no tools)
    numbered = _number(sources)
    structured = {}
    if text:
        data_struct = _post({"contents": [{"parts": [{"text": seo_structure_prompt(title, text, numbered)}]}],
                             "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192,
                                                  "responseMimeType": "application/json"}}, api_key, _urlopen)
        raw_struct = _text(data_struct)
        structured = _parse_json(raw_struct) if raw_struct else {}

    syn_val = (structured.get("synopsis_verbatim") or {}).get("value") if isinstance(structured.get("synopsis_verbatim"), dict) else (structured.get("synopsis_verbatim") or "")
    if syn_val == "NOT FOUND":
        syn_val = ""
    orig_val = (structured.get("series_name_original") or {}).get("value") if isinstance(structured.get("series_name_original"), dict) else (structured.get("series_name_original") or "")
    if orig_val == "NOT FOUND":
        orig_val = ""

    authors = structured.get("authors") or {}
    author_names = (authors.get("story") or {}).get("value") if isinstance(authors.get("story"), dict) else authors.get("story")
    artist_names = (authors.get("art") or {}).get("value") if isinstance(authors.get("art"), dict) else authors.get("art")
    publisher_val = (structured.get("publisher") or {}).get("value") if isinstance(structured.get("publisher"), dict) else structured.get("publisher")
    if publisher_val == "NOT FOUND":
        publisher_val = ""

    facts = {
        "english_title": structured.get("series_name_en") or title,
        "korean_title": orig_val,
        "search_names": structured.get("series_name_alt") or [],
        "author": author_names[0] if isinstance(author_names, list) and author_names else (author_names or ""),
        "artist": artist_names[0] if isinstance(artist_names, list) and artist_names else (artist_names or ""),
        "platform": publisher_val or "",
        "genres": structured.get("genres") or [],
        "keywords": structured.get("genre_tag_phrases") or structured.get("genres") or [],
        "synopsis": syn_val,
    }

    return {
        "at": time.time(),
        "model": MODEL,
        "text": text,
        "sources": sources,
        "claims": claims,
        "queries": meta.get("webSearchQueries", []),
        "structured": structured,
        "facts": facts,
    }


def structure_prompt(title, research_text, numbered_sources):
    return f"""Turn this research about the manhwa "{title}" into JSON. Use ONLY facts stated in the
research. Cite the source numbers that support each entry.

SOURCES:
{numbered_sources}

RESEARCH:
{research_text}

Return exactly this JSON object (no markdown):
{{"world_setting": {{"universe": "", "premise": "", "recurring_elements": "", "sources": []}},
 "characters": [{{"name": "", "aliases": [], "gender": "male|female|unknown", "pronouns": "he/him|she/her|they/them|unknown",
                  "role": "", "visual_cues": "", "relationships": "", "sources": []}}],
 "factions": [{{"name": "", "description": "", "sources": []}}],
 "terms": [{{"term": "", "meaning": "", "variants": []}}],
 "story_so_far": {{"text": "", "sources": []}},
 "disputes": [""]}}
Rules: at most 12 characters, most important first. gender/pronouns "unknown" unless the research
states them. Keep visual_cues to what a reader would SEE in a panel. Empty strings and lists are fine."""


def _number(sources):
    return "\n".join(f"[{i}] {s['domain']} ({s['tier']})" for i, s in enumerate(sources))


def _idx(i):
    """A cited source number: 3, "3" or "[3]" (the structurer is not consistent)."""
    if isinstance(i, int):
        return i
    m = re.fullmatch(r"\s*\[?(\d+)\]?\s*", str(i))
    return int(m.group(1)) if m else None


def _tiers(idx, sources):
    out = set()
    for i in idx or []:
        k = _idx(i)
        if k is not None and 0 <= k < len(sources):
            out.add(sources[k]["tier"])
    return out


def _name_keys(c):
    """Strings that identify a character in the research text: the name, its
    aliases, and the given name without the family name ("Seo Eunhyun" ->
    "Eunhyun"), which is how most sentences refer to them."""
    keys = []
    for n in [c.get("name")] + list(c.get("aliases") or []):
        n = (n or "").strip()
        if len(n) >= 3:
            keys.append(n.lower())
            parts = n.split()
            if len(parts) > 1 and len(" ".join(parts[1:])) >= 4:
                keys.append(" ".join(parts[1:]).lower())
    return keys


def _grounded(keys, claims):
    """Source numbers of the grounded sentences (Google's grounding supports)
    that mention any of `keys`. Owner, 2026-10-04: a whole cast (A Regressor's
    Tale of Cultivation, 9 named characters, the protagonist in 13 grounded
    sentences) was rejected because the structurer left its citations empty."""
    out = set()
    for cl in claims or []:
        t = (cl.get("text") or "").lower()
        if any(re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", t) for k in keys):
            out.update(cl.get("sources") or [])
    return sorted(out)


def _overlap_claims(text, claims, need=3):
    """Grounded sentences sharing >= `need` distinctive words with `text`."""
    words = {w for w in re.findall(r"[a-z]{5,}", (text or "").lower())}
    out = set()
    for cl in claims or []:
        cw = set(re.findall(r"[a-z]{5,}", (cl.get("text") or "").lower()))
        if len(words & cw) >= need:
            out.update(cl.get("sources") or [])
    return sorted(out)


def _parse_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1]) if i >= 0 and j > i else {}


# ------------------------------------------------------------------ research
def research(title, aliases, series_url, latest, window, api_key, _urlopen=None, _resolve=None):
    """Grounded research + structuring. Returns the research record."""
    data = _post({"contents": [{"parts": [{"text": research_prompt(title, aliases, series_url, latest, window)}]}],
                  "tools": [{"google_search": {}}],
                  "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192}}, api_key, _urlopen)
    text = _text(data)
    meta = ((data.get("candidates") or [{}])[0]).get("groundingMetadata") or {}
    res = _resolve or resolve
    sources = []
    for c in meta.get("groundingChunks", []):
        web = c.get("web") or {}
        url = res(web.get("uri", ""))
        host = domain(url) if "://" in url and "grounding-api-redirect" not in url else domain(web.get("title", ""))
        sources.append({"title": web.get("title", ""), "url": url, "domain": host, "tier": tier(host)})
    claims = [{"text": (s.get("segment") or {}).get("text", ""),
               "sources": s.get("groundingChunkIndices", [])} for s in meta.get("groundingSupports", [])]
    structured = structure(title, text, sources, api_key, _urlopen) if text else {}
    return {"at": time.time(), "model": MODEL, "text": text, "sources": sources, "claims": claims,
            "queries": meta.get("webSearchQueries", []), "structured": structured}


def restructure(title, saved, api_key, _urlopen=None):
    """Rebuild the cast from research already done (the saved text, sources and
    grounding): one structuring call, no new search."""
    structured = structure(title, saved.get("text", ""), saved.get("sources") or [], api_key, _urlopen)
    return {"at": saved.get("at"), "model": saved.get("model"), "text": saved.get("text", ""),
            "sources": saved.get("sources") or [], "claims": saved.get("claims") or [],
            "queries": saved.get("queries") or [], "structured": structured, "restructured_at": time.time()}


def structure(title, text, sources, api_key, _urlopen=None):
    """Research text -> bible JSON. Gemini 3.x THINKING tokens count toward
    maxOutputTokens: the first live run spent 7,866 of 8,192 on thinking and
    cut the JSON off after 319 tokens (5 of 14 series came back empty). So
    give it room, and retry once with double when it still stops at the limit."""
    budget = 32768
    for _ in range(2):
        d = _post({"contents": [{"parts": [{"text": structure_prompt(title, text, _number(sources))}]}],
                   "generationConfig": {"temperature": 0.1, "maxOutputTokens": budget,
                                        "responseMimeType": "application/json"}}, api_key, _urlopen)
        finish = ((d.get("candidates") or [{}])[0]).get("finishReason")
        try:
            out = _parse_json(_text(d))
            if out:
                return out
        except ValueError:
            pass
        if finish != "MAX_TOKENS":
            break
        budget *= 2
    return {}


def to_bible(title, aliases, rec):
    """Apply the source rules and produce bible fields from a research record."""
    st, src = rec.get("structured") or {}, rec.get("sources") or []
    chars, rejected = [], []
    for c in st.get("characters") or []:
        if not (c.get("name") or "").strip():
            continue
        t = _tiers(c.get("sources"), src) | _tiers(_grounded(_name_keys(c), rec.get("claims")), src)
        entry = {k: c.get(k) for k in ("name", "aliases", "gender", "pronouns", "role", "visual_cues",
                                       "relationships") if c.get(k)}
        entry["origin"] = "research"
        entry["source_tiers"] = sorted(t)
        if t & set(NAME_TIERS):
            chars.append(entry)
        else:
            rejected.append(entry["name"])
    ws = st.get("world_setting") or {}
    world_txt = " ".join(str(ws.get(k) or "") for k in ("universe", "premise", "recurring_elements"))
    world_ok = bool((_tiers(ws.get("sources"), src) | _tiers(_overlap_claims(world_txt, rec.get("claims")), src))
                    & set(FACT_TIERS))
    world = {k: ws.get(k) for k in ("universe", "premise", "recurring_elements") if ws.get(k)}
    sf = st.get("story_so_far") or {}
    return {
        "canonical_title": title,
        "aliases": aliases or [],
        "world_setting": world if world_ok else {},
        "characters": chars,
        "research": {
            "at": rec.get("at"), "model": rec.get("model"), "queries": rec.get("queries"),
            "text": rec.get("text", ""),      # kept: the cast list can be rebuilt without searching again
            "sources": src, "claims": rec.get("claims"),
            "world_unverified": {} if world_ok else world,
            "factions": st.get("factions") or [], "terms": st.get("terms") or [],
            "story_so_far": sf.get("text", ""),
            "story_verified": bool((_tiers(sf.get("sources"), src)
                                    | _tiers(_overlap_claims(sf.get("text", ""), rec.get("claims")), src))
                                   & set(FACT_TIERS)),
            "disputes": st.get("disputes") or [],
            "rejected_characters": rejected,
        },
    }


def _key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def merge(existing, researched):
    """Existing bible wins (hand-written / owner edits); research only adds."""
    if not existing:
        return researched
    out = json.loads(json.dumps(existing))
    have = {_key(c.get("name")) for c in out.get("characters", [])}
    for c in out.get("characters", []):
        have.update(_key(a) for a in c.get("aliases") or [])
    for c in researched.get("characters", []):
        if _key(c["name"]) not in have and not any(_key(a) in have for a in c.get("aliases") or []):
            out.setdefault("characters", []).append(c)
    ws = out.setdefault("world_setting", {})
    for k, v in (researched.get("world_setting") or {}).items():
        if not ws.get(k):
            ws[k] = v
    for a in researched.get("aliases") or []:
        if a not in out.setdefault("aliases", []):
            out["aliases"].append(a)
    out["research"] = researched.get("research")
    return out


def suggest_from_script(bible, script_text, min_mentions=2):
    """Names our own narration keeps using that the bible doesn't know yet —
    suggestions for the owner to confirm, never added on their own."""
    known = set()
    for c in (bible or {}).get("characters", []):
        known.add(_key(c.get("name")))
        known.update(_key(a) for a in c.get("aliases") or [])
        known.update(_key(p) for p in (c.get("name") or "").split())
    stop = {"the", "he", "she", "they", "his", "her", "but", "when", "as", "with", "in", "at",
            "a", "an", "it", "its", "even", "yet", "still", "then", "now", "back", "deep",
            "high", "inside", "across", "from", "down", "up", "over", "under", "before", "after"}
    starters = stop | {"later", "then", "meanwhile", "suddenly", "while", "after", "before", "once",
                       "finally", "instead", "despite", "though", "although", "because", "since", "until",
                       "only", "just", "every", "each", "both", "all", "some", "this", "that", "these",
                       "those", "their", "our", "my", "your", "behind", "beneath", "above", "below",
                       "through", "far", "soon", "here", "there", "without", "outside"}
    counts = {}
    for m in re.finditer(r"\b([A-Z][a-z]+(?:[ -][A-Z][a-z]+){0,3})\b", script_text or ""):
        name = m.group(1)
        words = name.split()
        while len(words) > 1 and words[0].lower() in starters:   # "Later Lord Mubon" -> "Lord Mubon"
            words = words[1:]
        name = " ".join(words)
        if name.lower() in stop or _key(name) in known:
            continue
        if m.start() == 0 or script_text[max(0, m.start() - 2):m.start()].strip() in (".", "!", "?"):
            if " " not in name:                 # a capitalised first word of a sentence
                continue
        counts[name] = counts.get(name, 0) + 1
    return sorted([n for n, k in counts.items() if k >= min_mentions], key=lambda n: -counts[n])
