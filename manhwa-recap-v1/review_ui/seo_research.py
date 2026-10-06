"""SEO web research per series, like Scrapper's LongForm "Research — facts &
sources" (owner, 2026-10-05: "do it" — SEO should do actual research).

What the SEO writer was missing: the names people actually SEARCH for (the
official English title, the Korean title, other translations), who made it,
where it's published, and the terms readers use. This asks Gemini with Google
Search grounding — the same engine and source grading as series_research.py,
which builds the cast list — then a second call (no tools) turns the answer
into fields, each citing its source numbers.

Source rules (series_research tiers):
  * credits (author, artist, platform, official English title) need an
    official or trusted source — they go into the description's credit line;
  * search names and keywords may come from any source except forums/social
    ("low"), because they are search vocabulary, not claims about the story.

Cached per series for CACHE_DAYS; two Gemini calls per series per refresh
(~1–3 cents, metered by usage.gate). Never raises to the caller's caller: the
SEO step carries on without it.
"""
import json
import os
import re
import sys
import time

_RECAP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RECAP not in sys.path:
    sys.path.insert(0, _RECAP)

import series_research as sr   # noqa: E402

CACHE_DIR = "_seo_web"
CACHE_DAYS = 7
CREDIT_TIERS = ("official", "trusted")
TERM_TIERS = ("official", "trusted", "wiki", "other")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:80] or "series"


def research_prompt(series, aliases, series_url):
    al = ", ".join(a for a in (aliases or []) if a) or "none known"
    return f"""Research the Korean webtoon / manhwa "{series}" (other names: {al}; a reader site: {series_url or "unknown"}).

Find, from the web:
1. The official English title, and the original Korean title (Hangul and romanised).
2. Every other title people search for it under (fan translations, shortened names, older names).
3. Author (story) and artist, and the original publisher / platform (e.g. Naver Webtoon, KakaoPage, Tapas).
4. Its genres and tags (e.g. murim, regression, system, cultivation).
5. The words and phrases fans use when they search for it or recommend it.
6. Its status (ongoing / completed) and roughly how many chapters.

Prefer the publisher's own pages, MangaUpdates, AniList, Novel Updates, Wikipedia and Namu Wiki. Say clearly when something is uncertain."""


def structure_prompt(series, text, numbered):
    return f"""From the research below about "{series}", return ONLY a JSON object:
{{
  "english_title": {{"value": "...", "sources": [n, ...]}},
  "korean_title": {{"value": "...", "sources": [...]}},
  "search_names": [{{"value": "...", "sources": [...]}}],
  "author": {{"value": "...", "sources": [...]}},
  "artist": {{"value": "...", "sources": [...]}},
  "platform": {{"value": "...", "sources": [...]}},
  "genres": [{{"value": "...", "sources": [...]}}],
  "keywords": [{{"value": "...", "sources": [...]}}],
  "status": {{"value": "...", "sources": [...]}}
}}
Use the source numbers from the list. Leave a value "" (or the list empty) when the research does not support it. Do not guess.

SOURCES
{numbered}

RESEARCH
{text}"""


def _tiers(nums, sources):
    out = set()
    for n in nums or []:
        try:
            i = int(n) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(sources):
            out.add(sources[i].get("tier"))
    return out


def _one(v, sources, allowed):
    if not isinstance(v, dict):
        return ""
    val = (v.get("value") or "").strip()
    return val if val and _tiers(v.get("sources"), sources) & set(allowed) else ""


def _many(vs, sources, allowed, limit=12):
    out = []
    for v in vs or []:
        val = _one(v, sources, allowed)
        if val and val.lower() not in {x.lower() for x in out}:
            out.append(val)
    return out[:limit]


def apply_rules(structured, sources):
    """Keep only what the source rules allow."""
    st = structured or {}
    return {
        "english_title": _one(st.get("english_title"), sources, CREDIT_TIERS),
        "korean_title": _one(st.get("korean_title"), sources, TERM_TIERS),
        "search_names": _many(st.get("search_names"), sources, TERM_TIERS),
        "author": _one(st.get("author"), sources, CREDIT_TIERS),
        "artist": _one(st.get("artist"), sources, CREDIT_TIERS),
        "platform": _one(st.get("platform"), sources, CREDIT_TIERS),
        "genres": _many(st.get("genres"), sources, TERM_TIERS, 8),
        "keywords": _many(st.get("keywords"), sources, TERM_TIERS, 15),
        "status": _one(st.get("status"), sources, TERM_TIERS),
    }


def research(series, aliases, series_url, api_key, _urlopen=None, _resolve=None):
    """Two gated Gemini calls: grounded research, then structuring."""
    data = sr._post({"contents": [{"parts": [{"text": research_prompt(series, aliases, series_url)}]}],
                     "tools": [{"google_search": {}}],
                     "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192}}, api_key, _urlopen)
    text = sr._text(data)
    meta = ((data.get("candidates") or [{}])[0]).get("groundingMetadata") or {}
    res = _resolve or sr.resolve
    sources = []
    for c in meta.get("groundingChunks", []):
        web = c.get("web") or {}
        url = res(web.get("uri", ""))
        host = sr.domain(url) if "://" in url and "grounding-api-redirect" not in url else sr.domain(web.get("title", ""))
        sources.append({"title": web.get("title", ""), "url": url, "domain": host, "tier": sr.tier(host)})
    structured = {}
    if text:
        numbered = "\n".join(f"{i + 1}. {s['domain']} — {s['title']}" for i, s in enumerate(sources))
        d = sr._post({"contents": [{"parts": [{"text": structure_prompt(series, text, numbered)}]}],
                      "generationConfig": {"temperature": 0.1, "maxOutputTokens": 16384,
                                           "responseMimeType": "application/json"}}, api_key, _urlopen)
        try:
            structured = sr._parse_json(sr._text(d))
        except ValueError:
            structured = {}
    return {"at": time.time(), "series": series, "sources": sources,
            "queries": meta.get("webSearchQueries", []), "facts": apply_rules(structured, sources),
            "error": None if text else "the web search returned nothing"}


def get(cache_root, series, aliases=(), series_url="", api_key=None, force=False, _research=None):
    """The cached record, refreshed when older than CACHE_DAYS (or force).
    Returns None when there's no series name or no key and nothing cached."""
    if not series:
        return None
    path = os.path.join(cache_root, CACHE_DIR, _slug(series) + ".json")
    cached = None
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        cached = None
    fresh = cached and (time.time() - (cached.get("at") or 0)) < CACHE_DAYS * 86400
    if fresh and not force:
        return {**cached, "from_cache": True}
    if not api_key:
        return cached
    rec = (_research or research)(series, list(aliases or []), series_url, api_key)
    if rec.get("error") and cached:
        return {**cached, "from_cache": True, "refresh_error": rec["error"]}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, path)
    return rec


def credit_line(facts):
    """A credit line for the description, from official/trusted facts only.
    No URLs (the channel never puts links in generated text)."""
    f = facts or {}
    title = f.get("english_title")
    if not title:
        return ""
    who = []
    if f.get("author") and f.get("artist") and f["author"] != f["artist"]:
        who = [f"{f['author']} (story)", f"{f['artist']} (art)"]
    elif f.get("author") or f.get("artist"):
        who = [f.get("author") or f.get("artist")]
    line = f"Original work: {title}"
    if f.get("korean_title"):
        line += f" ({f['korean_title']})"
    if who:
        line += " by " + " & ".join(who)
    if f.get("platform"):
        line += f", published on {f['platform']}"
    return line + ". Support the official release."
