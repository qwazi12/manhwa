"""Video description in fixed blocks — spec 06 Part B2.

  1. <= 150 chars: hook + exact series name + "Chapter {N}"      (most important)
  2. a unique 2-3 sentence summary of THIS chapter (the SEO writer's `summary`)
  3. timestamps, per ARC not per chapter (a chapter video is one arc -> none;
     a range compilation lists its arcs)
  4. playlist link + previous + next (only links that really exist)
  5. series info: title, ALL aliases (series pack), status, chapter count, credit
  6. the fixed footer, identical on every video (⚙️ Settings: description_footer)
  7. 3-5 hashtags

Block 2 is the inauthentic-content signal YouTube looks for when it repeats, so
`block2_overlap` compares it with the previous chapter's: word 3-gram overlap
above 80% is flagged (validate_publish turns it into a problem).
"""
import re

BLOCK1_MAX = 150
OVERLAP_MAX = 0.80
SEP = "\n\n"


def block1(hook, series, chapter):
    tail = f" | {series} Chapter {chapter}".rstrip() if series else f" | Chapter {chapter}"
    room = BLOCK1_MAX - len(tail)
    hook = " ".join((hook or "").split())
    if len(hook) > room:
        hook = hook[:max(0, room - 1)].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return (hook + tail).strip(" |")[:BLOCK1_MAX]


def summary_from(text, max_sentences=3):
    """Block 2 from the writer's summary (or the first sentences of its
    description, for records made before `summary` existed)."""
    t = " ".join((text or "").split())
    sents = re.split(r"(?<=[.!?])\s+", t)
    return " ".join(s for s in sents[:max_sentences] if s).strip()


def block3(arcs):
    """arcs: [(seconds, label)] — only when there are 2+ arcs."""
    if not arcs or len(arcs) < 2:
        return ""
    def ts(s):
        s = int(s)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
    rows = [(0, arcs[0][1])] + list(arcs[1:]) if arcs[0][0] != 0 else list(arcs)
    return "\n".join(f"{ts(s)} {label}" for s, label in rows)


def block4(playlist_url="", prev_url="", next_url=""):
    rows = []
    if playlist_url:
        rows.append(f"▶ Full series playlist: {playlist_url}")
    if prev_url:
        rows.append(f"⏮ Previous chapter: {prev_url}")
    if next_url:
        rows.append(f"⏭ Next chapter: {next_url}")
    return "\n".join(rows)


def block5(pack, chapter_count=None):
    p = pack or {}
    rows = []
    if p.get("title_en"):
        rows.append(f"Series: {p['title_en']}")
    if p.get("aliases"):
        rows.append("Also known as: " + ", ".join(p["aliases"]))
    if p.get("status"):
        rows.append(f"Status: {p['status']}")
    n = chapter_count or p.get("total_chapters")
    if n:
        rows.append(f"Chapters: {n}")
    cr = p.get("credit") or {}
    who = [x for x in (cr.get("author"), cr.get("artist")) if x]
    if who:
        line = "Original work by " + " & ".join(dict.fromkeys(who))
        if cr.get("platform"):
            line += f" ({cr['platform']})"
        rows.append(line + ". Support the official release.")
    return "\n".join(rows)


def block7(hashtags):
    tags, seen = [], set()
    for h in hashtags or []:
        h = "#" + re.sub(r"[^\w]", "", str(h).lstrip("#"))
        if len(h) > 1 and h.lower() not in seen:
            seen.add(h.lower())
            tags.append(h)
    return " ".join(tags[:5])


def build(*, hook, series, chapter, summary, pack=None, arcs=None, playlist_url="", prev_url="",
          next_url="", footer="", hashtags=(), chapter_count=None, limit=5000):
    blocks = [block1(hook, series, chapter), summary_from(summary), block3(arcs),
              block4(playlist_url, prev_url, next_url), block5(pack, chapter_count),
              (footer or "").strip(), block7(hashtags)]
    return SEP.join(b for b in blocks if b)[:limit]


def _grams(text, n=3):
    w = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def block2_overlap(a, b, n=3):
    """Share of `a`'s word n-grams that also occur in `b` (0..1)."""
    ga, gb = _grams(a, n), _grams(b, n)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(len(ga))


def templated(a, b):
    return block2_overlap(a, b) > OVERLAP_MAX
