"""Series asset pack — spec 06 Part B4.

One record per series in `projects/_series_packs/<series_id>.json`:

  series_id, title_en, aliases[], characters[], genre[], source_url,
  total_chapters, status, playlist_id, thumbnail_dna

Aliases are the highest-value untapped asset (YouTube documents tags for
misspellings and name variants), so they are gathered from EVERY source the
studio already has, never invented:
  * the web research (seo_research: Google-grounded search_names, the Korean
    and official English titles)
  * the Series Bible (series-level aliases)
  * the watchlist entry (owner-entered aliases)
  * the URL slug / apostrophe form (seo._aliases)
  * `aliases_manual`, which the owner edits and which a refresh never drops.
Consumed by: tags (aliases appended within YouTube's 500-char cap), the
description's block 5 (description_blocks.py) and the narration's opening line
(one alias per chapter, rotating — narrate.py).
"""
import json
import os
import re
import time

DIR = "_series_packs"
FIELDS = ("series_id", "title_en", "aliases", "characters", "genre", "source_url",
          "total_chapters", "status", "playlist_id", "thumbnail_dna")


def _path(root, series_id):
    safe = re.sub(r"[^a-z0-9\-]+", "-", (series_id or "").lower()).strip("-") or "unknown"
    return os.path.join(root, DIR, safe + ".json")


def load(root, series_id):
    try:
        with open(_path(root, series_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(root, pack):
    p = _path(root, pack.get("series_id"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pack, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)
    return pack


def merge_aliases(title_en, *groups):
    """Every distinct name, in source order, minus the English title itself."""
    out, seen = [], {(title_en or "").strip().lower()}
    for g in groups:
        for a in g or []:
            a = " ".join(str(a or "").split()).strip(" .,;")
            if len(a) < 2 or a.lower() in seen:
                continue
            seen.add(a.lower())
            out.append(a)
    return out


# Keys only the owner (or an owner-approved step) sets; a refresh never overwrites them.
OWNER_KEYS = ("aliases_manual", "title_lock", "hook_candidates", "playlist_id", "owner_edits",
              "title_template", "description_template", "default_tags", "default_privacy",
              "default_targets", "default_category", "default_made_for_kids",
              "default_synthetic_disclosure", "music_credit", "recon")


def build(series_id, *, series="", web_facts=None, bible=None, watch=None, slug_aliases=(),
          source_url="", total_chapters=None, status="", playlist_id="", style=None, prev=None):
    """A fresh pack from the sources; owner edits in `prev` survive."""
    wf = web_facts or {}
    prev = prev or {}
    title_en = wf.get("english_title") or (watch or {}).get("title") or series
    manual = list(prev.get("aliases_manual") or [])
    aliases = merge_aliases(title_en,
                            manual,
                            [series] if series and series != title_en else [],
                            wf.get("search_names"),
                            [wf.get("korean_title")] if wf.get("korean_title") else [],
                            (bible or {}).get("aliases"),
                            (watch or {}).get("aliases"),
                            slug_aliases)
    chars = [{"name": c.get("name"), "role": c.get("role") or "",
              "aliases": list(c.get("aliases") or [])}
             for c in (bible or {}).get("characters") or [] if c.get("name")]
    dna = None
    if style:
        dna = {"composition": style.get("composition"), "palette": style.get("palette"),
               "badge": style.get("badge"), "approved": bool(style.get("approved"))}
    # Spec 10 §7 series cache fields, derived from the same sources. Korean
    # (non-Latin) names are the series_name_ko; Latin ones are the alternates.
    ko = wf.get("korean_title") or next((a for a in aliases if not re.search(r"[A-Za-z]", a)), "")
    # a character's name (or alias) is never an alternate TITLE (live 2026-10-08:
    # "Dong Bongsu" showed up among Murim Psychopath's titles)
    people = {n.lower() for c in chars for n in [c["name"]] + list(c.get("aliases") or [])}
    # ... and the series' own name re-spelled (URL slug + site id, a guessed
    # apostrophe: "The Regressed Mercenary Ha's A Plan 7261") isn't one either
    core = lambda x: re.sub(r"[^a-z]", "", re.sub(r"\s+\d+\s*$", "", (x or "").lower()))
    own = {core(title_en), core(series)}
    alt = [a for a in aliases if re.search(r"[A-Za-z]", a) and core(a) not in own and a.lower() not in people]
    mc = [c["name"] for c in chars if "protagonist" in (c.get("role") or "").lower()]
    spec = {
        "series_name_en": title_en,
        "series_name_alt": alt,
        "series_name_ko": ko,
        "series_hashtag": re.sub(r"[^A-Za-z0-9]", "", "".join(w[:1].upper() + w[1:] for w in
                                                             re.findall(r"[A-Za-z0-9']+", title_en or ""))),
        "authors": {"story": [wf["author"]] if wf.get("author") else [],
                    "art": [wf["artist"]] if wf.get("artist") else []},
        "publisher": wf.get("platform") or "",
        "genres": list(wf.get("genres") or []),
        "characters_main": mc + [c["name"] for c in chars if c["name"] not in mc][:3],
    }
    fresh = {
        "series_id": series_id,
        "title_en": title_en,
        "aliases": aliases,
        "aliases_manual": manual,
        "characters": chars,
        "genre": list(wf.get("genres") or []),
        "source_url": source_url or prev.get("source_url") or "",
        "total_chapters": total_chapters if total_chapters is not None else prev.get("total_chapters"),
        "status": status or wf.get("status") or prev.get("status") or "",
        "playlist_id": playlist_id or prev.get("playlist_id") or "",
        "thumbnail_dna": dna or prev.get("thumbnail_dna"),
        "credit": {k: wf.get(k) for k in ("author", "artist", "platform", "korean_title") if wf.get(k)},
        "updated_at": time.time(),
        **spec,
    }
    # Everything the OWNER set (series defaults: title_template, default_tags,
    # privacy …; spec 10: title_lock, hook candidates, playlist, owner edits of
    # the series fields) survives a refresh. Bug fixed 2026-10-08: build()
    # returned a fresh dict, so the next refresh wiped "Save as series defaults".
    out = dict(prev)
    out.update(fresh)
    for k in OWNER_KEYS:
        if k in prev:
            out[k] = prev[k]
    for k, v in (prev.get("owner_edits") or {}).items():
        out[k] = v
    return out


def tags_with_aliases(tags, aliases, cap=500):
    """Append aliases to the tag list (dedup, case-insensitive) within
    YouTube's 500-character total (tags + separators)."""
    out = list(tags or [])
    have = {t.lower() for t in out}
    total = sum(len(t) for t in out) + max(0, len(out) - 1)
    for a in aliases or []:
        a = " ".join(re.sub(r"[<>,]", " ", a).split())     # a YouTube tag can't hold , < >
        if not a or a.lower() in have:
            continue
        if total + len(a) + 1 > cap:
            continue
        out.append(a)
        have.add(a.lower())
        total += len(a) + 1
    return out


def spoken_alias(pack, chapter):
    """One alias for the narration's opening line, rotating by chapter so a
    series' whole alias list is spoken across its chapters."""
    names = [a for a in (pack or {}).get("aliases") or [] if re.search(r"[A-Za-z]", a)]
    if not names:
        return ""
    try:
        n = int(re.sub(r"\D", "", str(chapter)) or 0)
    except ValueError:
        n = 0
    return names[n % len(names)]
