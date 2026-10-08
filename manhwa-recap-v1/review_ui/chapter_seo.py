"""Per-chapter SEO package — docs/audit/10_SEO_SYSTEM_SPEC.md (owner, 2026-10-08).

Division of labour (spec §8): the TITLE has 72 characters and its job is
EMOTION — a series-locked hook, only the chapter number changes per upload.
The DESCRIPTION and TAGS have ~5,000 characters and their job is LITERAL
MATCHING — "{Series} Chapter {N}" verbatim plus variants catch release-day
searchers. The old keyword-first title tried to do both in 72 characters and
did neither well.

Pure functions only (no network, no model): title skeleton (§4.1, T1/T2),
description template with the frozen boilerplate (§4.2, §1.1, D1-D5), hidden
tags in three blocks (§4.3, H1/H2), exactly three hashtags (§4.4, H3/H4) and
the validation suite run before approval. Thumbnails are out of scope
(owner, 2026-10-08: "no need to thumbnails").
"""
import re

TITLE_MAX = 72
TITLE_TARGET = 70
HOOK_MIN_BUDGET = 20
SUFFIX = " | Manhwa Recap"
DASH = " — "
TAGS_MAX = 500
HASHTAGS_FIRST = "#ManhwaRecap"
HASHTAGS_LAST = "#MangaRecap"
CHANNEL = "Flamingo Recap"

# §1.1 — frozen, byte-identical every upload (D3). Never regenerated.
KOFI = ("❤️ Support the channel — Ko-fi: https://ko-fi.com/flamingorecap\n"
        "Your support keeps the grind alive and the recaps coming. Drop a comment with any recaps "
        "you'd love to see — we're always listening!")
SUBSCRIBE = "🔥 New recaps every week! Subscribe: https://youtube.com/@flamingoremix?si=iE3SIai9AooDNePA"
MUSIC = "🎵 Background music: {music_credit}\nLicensed under Creative Commons Attribution License (reuse allowed)"
DISCLAIMER = ("© Disclaimer:\n"
              "The content on Flamingo Recap is created to share and promote stories and creators through "
              "recaps and narrations. All rights to the featured content belong to their respective owners, "
              "and no ownership is claimed over content not created by this channel. If you have any concerns "
              "regarding the use of your content, please contact us at kymedia.mgmt@gmail.com, and we will "
              "promptly address the matter.")
FAIR_USE = ("© Fair Use:\n"
            "This channel may utilize copyrighted material that falls under the fair use provisions for "
            "purposes such as criticism, commentary, news reporting, teaching, scholarship, or research. "
            "Fair use is permitted by copyright law and does not infringe on the rights of the original "
            "copyright holders.")
# §4.3 — frozen channel-wide
EVERGREEN = ["manhwa recap", "manga recap", "manhwa chapter recap", "manhwa explained", "webtoon recap",
             "korean webtoon", "manhwa summary", "manhwa compilation", "full manhwa recap", "flamingo recap"]
# §5 — blacklisted filler constructions ("His Class Change Shocks Murim")
BLACKLIST = re.compile(r"\b(shocks?|shocked|stuns?|stunned|shakes?)\b", re.I)


def _num(n):
    f = float(n)
    return str(int(f)) if f.is_integer() else str(f)


def hashtag_for(name):
    """CamelCase hashtag body: 'Murim Psychopath' -> 'MurimPsychopath'."""
    words = re.findall(r"[A-Za-z0-9]+", (name or "").replace("'", "").replace("’", ""))
    return "".join(w[:1].upper() + w[1:] for w in words)


# ------------------------------------------------------------------ title
def long_title(n, hook, series):
    return f"[{_num(n)}] {hook}{DASH}{series}{SUFFIX}"


def short_title(n, hook):
    return f"[{_num(n)}] {hook}{SUFFIX}"


def hook_budget(series, n=99):
    """Characters left for the hook in the long form for chapter n (exact,
    by length — the spec's formula assumes a 2-digit chapter)."""
    return TITLE_MAX - len(long_title(n, "", series))


def title_variant(series, hook, n=999):
    """'long' or 'short' — chosen once per series (T3): the long form when
    the hook fits it for any chapter number up to n, else the short fallback."""
    return "long" if len(hook or "") <= hook_budget(series, n) and hook_budget(series, n) >= HOOK_MIN_BUDGET \
        else "short"


def build_title(n, title_lock, series):
    """T1/T2: the locked skeleton, only the chapter number varies."""
    hook = (title_lock or {}).get("series_hook") or ""
    variant = (title_lock or {}).get("variant") or title_variant(series, hook)
    t = long_title(n, hook, series) if variant == "long" else short_title(n, hook)
    return t


# ------------------------------------------------------------------ tags
def chapter_block(series, n):
    s, c = (series or "").lower(), _num(n)
    return [f"{s} chapter {c}", f"{s} ch {c}", f"{s} {c}", f"{s} chapter {c} english",
            f"{s} new chapter", f"{s} latest chapter"]


def series_block(pack):
    """Series name + ALL alternate titles + Korean title + MC name + creators
    + season names + genres as tag phrases (H2), deduped, frozen per series."""
    p = pack or {}
    name = (p.get("series_name_en") or p.get("title_en") or "").strip()
    out = [f"{name.lower()} manhwa"] if name else []
    out += [a for a in (p.get("series_name_alt") or p.get("aliases") or [])]
    if p.get("series_name_ko"):
        out.append(p["series_name_ko"])
    out += [c if isinstance(c, str) else c.get("name") for c in (p.get("characters") or [])[:3]]
    a = p.get("authors") or {}
    out += list(a.get("story") or []) + list(a.get("art") or [])
    out += [s.get("name") for s in p.get("seasons") or [] if isinstance(s, dict) and s.get("name")]
    out += [f"{g.lower()} manhwa" for g in (p.get("genres") or p.get("genre") or [])]
    seen, res = set(), []
    for t in out:
        t = " ".join(re.sub(r"[<>,]", " ", str(t or "")).split()).lower()
        if t and t not in seen:
            seen.add(t)
            res.append(t)
    return res


def build_tags(series, n, pack, limit=TAGS_MAX):
    """H1: chapter block (never trimmed) + series block + evergreen block,
    <= 500 characters (tags + separating commas); overflow trims the evergreen
    tail first, then the series block's tail."""
    ch, se, ev = chapter_block(series, n), series_block(pack), list(EVERGREEN)
    size = lambda xs: sum(len(x) for x in xs) + max(0, len(xs) - 1)
    seen = set()
    blocks = []
    for blk in (ch, se, ev):
        b = []
        for t in blk:
            if t.lower() not in seen:
                seen.add(t.lower())
                b.append(t)
        blocks.append(b)
    ch, se, ev = blocks
    while ev and size(ch + se + ev) > limit:
        ev.pop()
    while se and size(ch + se) > limit:
        se.pop()
    return ch + se + ev


# ------------------------------------------------------------------ hashtags
def hashtags(series):
    """H3: exactly three, #ManhwaRecap first."""
    return [HASHTAGS_FIRST, "#" + hashtag_for(series), HASHTAGS_LAST]


# ------------------------------------------------------------------ timecodes
def _secs(t):
    parts = [int(x) for x in str(t).split(":")]
    s = 0
    for p in parts:
        s = s * 60 + p
    return s


def valid_timecodes(tcs, video_seconds=None):
    """D4: >= 3 entries, the first at 0:00, each section >= 10 s, labels are
    story beats (never 'Part N'). Returns the list, or [] when it fails."""
    tcs = [t for t in tcs or [] if t.get("t") is not None and t.get("label")]
    if len(tcs) < 3 or _secs(tcs[0]["t"]) != 0:
        return []
    starts = [_secs(t["t"]) for t in tcs] + ([int(video_seconds)] if video_seconds else [])
    if any(b - a < 10 for a, b in zip(starts, starts[1:])):
        return []
    if any(re.match(r"(?i)^\s*(old |new )?part\s*\d*\s*$", t["label"]) for t in tcs):
        return []
    return tcs


def timecode_block(tcs):
    if not tcs:
        return ""
    return "⏱️ Timestamps:\n" + "\n".join(f"• {t['t']} - {t['label']}" for t in tcs)


# ------------------------------------------------------------------ description
def line1(series, n, chapter_title, tease):
    """D1: exact phrase + (English) + chapter title + non-spoiler tease + channel."""
    c = _num(n)
    head = f"{series} Chapter {c} recap (English)"
    if chapter_title:
        head += f" — \"{chapter_title}\"!"
    else:
        head += "!"
    tease = " ".join((tease or "").split())
    return " ".join(x for x in (head, tease, f"Full chapter {c} recap on {CHANNEL}!") if x)


def line2(prev_n, prev_link, playlist_link):
    """D2: previous chapter + playlist (only links that exist)."""
    parts = []
    if prev_link and prev_n is not None:
        parts.append(f"Watch Chapter {_num(prev_n)} here: {prev_link}")
    if playlist_link:
        parts.append(f"Start from Chapter 1: {playlist_link}")
    return " • ".join(parts)


def series_lines(pack):
    p = pack or {}
    name = p.get("series_name_en") or p.get("title_en") or ""
    alts = [a for a in (p.get("series_name_alt") or []) if a]
    names = " / ".join(x for x in [", ".join(alts[:4]), p.get("series_name_ko") or ""] if x)
    out = [f"📖 Series: {name}" + (f" ({names})" if names else "")]
    a = p.get("authors") or {}
    cred = []
    if a.get("story"):
        cred.append("✍️ Story: " + ", ".join(a["story"]))
    if a.get("art"):
        cred.append("🎨 Art: " + ", ".join(a["art"]))
    if p.get("publisher"):
        cred.append("Original publisher: " + p["publisher"])
    if cred:
        out.append(" • ".join(cred))
    return "\n".join(out)


def build_description(series, n, pack, *, chapter_title="", tease="", prev_n=None, prev_link="",
                      playlist_link="", timecodes=None, music_credit=""):
    blocks = [line1(series, n, chapter_title, tease)]
    l2 = line2(prev_n, prev_link, playlist_link)
    top = blocks[0] + ("\n" + l2 if l2 else "")
    body = [top, series_lines(pack) + "\n" + SUBSCRIBE]
    tc = timecode_block(timecodes)
    if tc:
        body.append(tc)
    body.append(KOFI)
    if music_credit:                       # C2: only when the render used music
        body.append(MUSIC.format(music_credit=music_credit))
    body += [DISCLAIMER, FAIR_USE, " ".join(hashtags(series))]
    return "\n\n".join(body)


# ------------------------------------------------------------------ validation
UNSTABLE = re.compile(r"\b\d{2,4}\s+(novel\s+)?chapters\b|\bchapters?\s+total\b|\btotal\s+chapters\b", re.I)


def validate(pkg, series, n):
    """The approval-blocking suite (§9 Stage 3): T2, D1, D3, D4, D5, H1, H3, H4.
    Returns human sentences ([] == fine)."""
    out = []
    t = pkg.get("title") or ""
    if len(t) > TITLE_MAX:
        out.append(f"Title is {len(t)} characters; the series format allows {TITLE_MAX} (T2).")
    if not t.startswith(f"[{_num(n)}] ") or not t.endswith(SUFFIX):
        out.append("Title doesn't follow the series' locked format \"[N] hook — series | Manhwa Recap\" (T1).")
    d = pkg.get("description") or ""
    first = d.split("\n", 1)[0]
    if f"{series} Chapter {_num(n)}" not in first or "(English)" not in first:
        out.append(f"Description line 1 must contain \"{series} Chapter {_num(n)}\" and \"(English)\" (D1).")
    for blk, name in ((KOFI, "Ko-fi"), (DISCLAIMER, "© Disclaimer"), (FAIR_USE, "© Fair Use"),
                      (SUBSCRIBE, "subscribe line")):
        if blk not in d:
            out.append(f"The frozen {name} block is missing or was changed (D3/C1).")
    if UNSTABLE.search(d):
        out.append("The description cites a chapter count — those go stale and conflict between sources (D5).")
    tags = pkg.get("tags") or []
    size = sum(len(x) for x in tags) + max(0, len(tags) - 1)
    if size > TAGS_MAX:
        out.append(f"Tags total {size} characters; the limit is {TAGS_MAX} (H1).")
    if chapter_block(series, n)[0] not in [x.lower() for x in tags]:
        out.append("The chapter tag block is missing (H1).")
    hs = re.findall(r"(?<![\w#])#\w+", d)
    if len(hs) != 3 or hs[0] != HASHTAGS_FIRST:
        out.append(f"Exactly 3 hashtags, #ManhwaRecap first — found {len(hs)} (H3/H4).")
    return out


def hook_problems(hook, series):
    """§5 rules a series hook must pass before it can be locked."""
    out = []
    h = " ".join((hook or "").split())
    if not h:
        return ["empty hook"]
    if BLACKLIST.search(h):
        out.append("uses a blacklisted filler verb (shocks/stuns …)")
    if len(h) > hook_budget(series, 999) and len(short_title(999, h)) > TITLE_MAX:
        out.append(f"too long: {len(h)} characters (the short form allows {TITLE_MAX - len(short_title(999, ''))})")
    if re.search(r"(?i)\bchapter\s*\d", h):
        out.append("names a chapter — the hook is series-level (T4)")
    return out
