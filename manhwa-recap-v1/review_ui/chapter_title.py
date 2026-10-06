"""Chapter video titles — spec 06 Part B1 (docs/audit/06_REVISION_perChapter_and_Thumbnails.md).

    "{EVENT HOOK} - {Series} Chapter {N} Manhwa Recap"

The hook comes FIRST and comes from the chapter's own content: the SEO writer
returns only the hook (seo.py), and without it the hook is derived from the
chapter's narration by thumbnail_studio.hook_from_title — the same function the
thumbnail uses, so title and thumbnail tell the same story. 100 characters max
(the hook is shortened at a word to fit; series and chapter never are).

The validator rejects, with a sentence the publish form shows:
  * the old machine pattern "P{n} | ... | #manhwa" (two channels published that
    exact string and both got ~1.5k views);
  * a title with no hook ("Chapter N" alone);
  * a title identical to another export's title;
  * more than 100 characters.
"""
import os
import re

TEMPLATE = "{hook} - {series} Chapter {chapter} Manhwa Recap"
# Formats this studio shipped before B1; a saved setting still holding one of
# them is read as the new format (the owner never chose them per se — they
# were the defaults).
OLD_TEMPLATES = ("{hook} | {series} Ch.{chapter}",)
LIMIT = 100
OLD_PATTERN = re.compile(r"^\s*P\s*\d+\s*\|.*\|.*#manhwa\b", re.I)


HOOK_MIN_ROOM = 20


def build(hook, series, chapter, limit=LIMIT, template=TEMPLATE):
    """The title from the format, <= `limit`. Overflow (the spec gives no rule
    for chapter titles; this mirrors B3's "drop the tail first"): drop
    " Manhwa Recap", then shorten the series at a word, so the hook always keeps
    at least HOOK_MIN_ROOM characters and the chapter number is never cut."""
    import studio_settings as _ss
    series = series or ""
    fixed = lambda tp, se: len(tp.replace("{hook}", "").replace("{series}", se)
                               .replace("{chapter}", str(chapter or "")))
    if fixed(template, series) > limit - HOOK_MIN_ROOM and template.endswith(" Manhwa Recap"):
        template = template[:-len(" Manhwa Recap")]
    while series and fixed(template, series) > limit - HOOK_MIN_ROOM:
        cut = series.rstrip("…").rsplit(" ", 1)[0]
        series = (cut + "…") if cut and cut != series.rstrip("…") else ""
    return _ss.build_title(template, hook, series, chapter, limit)


def hook_from_content(text):
    """A hook from the chapter's own words (narration), via the thumbnail's
    hook_from_title — not a second implementation. Title-cased words, the
    channel's ALLCAPS emphasis is left to the SEO writer."""
    import thumbnail_studio as ts
    first = re.split(r"(?<=[.!?])\s+", (text or "").strip())[0] if text else ""
    h = ts.hook_from_title(first)
    return " ".join(w.capitalize() for w in h.split())


def _norm(t):
    return " ".join((t or "").lower().split())


def hook_part(title, series, chapter):
    """What precedes ' - {series} Chapter {N}' (or the whole title when the
    format was not used)."""
    t = (title or "").strip()
    m = re.search(r"\s[-|–—]\s*" + re.escape(series or "") + r"\b", t, re.I) if series else None
    head = t[:m.start()] if m else t
    head = re.sub(r"(?i)\b(chapter|ch\.?)\s*\d+\b", "", head)
    head = re.sub(r"(?i)\bmanhwa recap\b|\brecap\b", "", head)
    if series:
        head = re.sub(re.escape(series), "", head, flags=re.I)
    return " ".join(re.sub(r"[|\-–—:#]+", " ", head).split())


def problems(title, series="", chapter="", others=()):
    """Human sentences for every rule the title breaks ([] == fine)."""
    t = (title or "").strip()
    out = []
    if not t:
        return ["A title is required."]
    if len(t) > LIMIT:
        out.append(f"Title is {len(t)} characters; the limit is {LIMIT}.")
    if OLD_PATTERN.search(t):
        out.append("Title uses the old \"P{n} | … | #manhwa\" pattern — use "
                   "\"{hook} - {series} Chapter {N} Manhwa Recap\".")
    elif len(hook_part(t, series, chapter).split()) < 2:
        out.append("Title has no hook — it must lead with what happens in the chapter, "
                   "never \"Chapter N\" alone.")
    if any(_norm(t) == _norm(o) for o in others if o):
        out.append("Another video already has exactly this title — make the hook unique to this chapter.")
    return out


def existing_titles(projects_root, skip=None, draft_name="_draft", skip_project=None):
    """Every export's title across all projects, except `skip` = (pid, name)
    and every export of `skip_project`."""
    import json
    out = []
    try:
        pids = os.listdir(projects_root)
    except OSError:
        return out
    for pid in pids:
        p = os.path.join(projects_root, pid, "publish.json")
        if pid.startswith("_") or pid == skip_project or not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                store = json.load(f)
        except (OSError, ValueError):
            continue
        for name, md in (store or {}).items():
            if name == draft_name or (skip and (pid, name) == tuple(skip)):
                continue
            if isinstance(md, dict) and md.get("title"):
                out.append(md["title"])
    return out
