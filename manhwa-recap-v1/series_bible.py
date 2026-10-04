"""Series Bible — Canonical universe, character cast, and SEO metadata.

Acts as the single source of truth across the entire recap studio:
1. panel-describe: Injects character visual cues & costume-vs-monster rules so vision models
   tag canonical names and do not mistake props/costumes for demons.
2. narrate.py: Injects canonical names, pronouns, and relationship dynamics, eliminating
   misgendering and generic \"the guy\" amnesia.
3. seo.py: Feeds canonical titles, characters, aliases, and high-volume keywords directly
   to the YouTube/TikTok packaging engine.
4. review_ui: Provides an operator-editable cast and setting sheet that persists across chapters.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTS = os.path.join(HERE, "review_ui", "projects")
BIBLES_DIR = os.path.join(PROJECTS, "_series_bibles")

# Built-in seed data for canonical series
DEFAULT_BIBLES = {
    "murim-psychopath": {
        "series_id": "murim-psychopath",
        "canonical_title": "Murim Psychopath",
        "aliases": [
            "Crazy Demon",
            "Psychopath of the Murim",
            "The Mad Demon",
            "무림싸이코패스"
        ],
        "world_setting": {
            "universe": "Murim / Martial Arts",
            "premise": "A modern psychopath awakens in the body of a noble clan heir in the ruthless martial world, tearing through rival factions with unpredictable cunning and razor-sharp intellect.",
            "recurring_elements": "Martial sects, internal qi, festival performers and lion-dance costumes (not real monsters), assassins.",
            "costume_vs_monster_rule": "In festival, celebration, tournament, or street crowd scenes, dragon and lion heads, puppets, and masks are worn by martial performers and acrobats. They are STAGE PROPS and COSTUMES, NOT demonic entities, golems, or mythical monsters."
        },
        # Corrected 2026-10-04 from sourced research + ch.44 OCR: the hero is Dong
        # Bongsu living as "Sosam" (alias Tang Sam) — "Yu Shin" never appears in the
        # comic — and the Cursed Killing Star is Yeon Yeong-ha's title, not a
        # separate person (OCR: "HOW DID YEON YOUNGHA KNOW…", "C-CURSED KILLING STAR?").
        "characters": [
            {
                "name": "Dong Bongsu",
                "aliases": ["Sosam", "Tang Sam", "Dong Bong-su", "The Nameless Swordsman"],
                "gender": "male",
                "role": "protagonist",
                "visual_cues": "Wild dark hair tied back, dark noble robes with fur collar/trim, deadpan or sarcastic psychotic smirk, fierce martial stance",
                "pronouns": "he/him"
            },
            {
                "name": "Yeon Yeong-ha",
                "aliases": ["Cursed Killing Star", "Incarnation of the Extreme Yin Cursed Killing Star", "Yeon Young-ha", "Yeon"],
                "gender": "female",
                "role": "rival / assassin",
                "visual_cues": "Long wild dark hair, fitted dark martial attire, sharp feminine gaze, red blade or blood-red aura, glowing crimson eyes",
                "pronouns": "she/her"
            }
        ],
        "seo_metadata": {
            "core_hook": "When a clinical psychopath reincarnates into the most ruthless martial arts clan...",
            "high_volume_keywords": [
                "murim psychopath",
                "manhwa recap",
                "recap manhwa",
                "overpowered mc",
                "ruthless mc",
                "murim manhwa recap"
            ],
            "hashtags": [
                "#murimpsychopath",
                "#manhwarecap",
                "#manhwa",
                "#animerecap",
                "#overpoweredmc"
            ]
        }
    }
}


def slugify(text):
    text = (text or "").lower().strip()
    text = re.sub(r"-[a-f0-9]{8}$", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "unknown-series"


def ensure_bibles_dir():
    os.makedirs(BIBLES_DIR, exist_ok=True)
    return BIBLES_DIR


def get_bible_path(series_id):
    slug = slugify(series_id)
    return os.path.join(ensure_bibles_dir(), f"{slug}.json")


def load_series_bible(series_id, pdir=None):
    """Load the Series Bible for a given series_id.
    Checks:
      1. <pdir>/series_bible.json (project-local override)
      2. _series_bibles/<slug>.json (canonical store)
      3. DEFAULT_BIBLES (in-memory seeds)
    Returns dict or None.
    """
    slug = slugify(series_id)
    if pdir:
        local_p = os.path.join(pdir, "series_bible.json")
        if os.path.exists(local_p):
            try:
                with open(local_p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

    global_p = get_bible_path(slug)
    if os.path.exists(global_p):
        try:
            with open(global_p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    if slug in DEFAULT_BIBLES:
        # Seed to disk
        bible = dict(DEFAULT_BIBLES[slug])
        save_series_bible(slug, bible)
        return bible

    # Fuzzy match on aliases or title
    for key, item in DEFAULT_BIBLES.items():
        if slug == key or slug in slugify(item.get("canonical_title", "")):
            save_series_bible(key, item)
            return item
        for alias in item.get("aliases", []):
            if slug == slugify(alias):
                save_series_bible(key, item)
                return item

    return None


def save_series_bible(series_id, data, pdir=None):
    """Save a Series Bible to canonical storage and optionally project directory."""
    slug = slugify(series_id or data.get("series_id"))
    data["series_id"] = slug
    ensure_bibles_dir()
    global_p = os.path.join(BIBLES_DIR, f"{slug}.json")
    with open(global_p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if pdir and os.path.isdir(pdir):
        local_p = os.path.join(pdir, "series_bible.json")
        with open(local_p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    return global_p


def format_for_vision(bible):
    """Format the Series Bible into a compact prompt addition for panel-describe."""
    if not bible:
        return ""
    lines = [
        "\n--- KNOWN SERIES CONTEXT (SERIES BIBLE) ---",
        f"Series: {bible.get("canonical_title", "Unknown")} ({bible.get("world_setting", {}).get("universe", "Manhwa")})",
    ]
    rule = bible.get("world_setting", {}).get("costume_vs_monster_rule")
    if rule:
        lines.append(f"Setting & Prop Rules: {rule}")

    chars = bible.get("characters", [])
    if chars:
        lines.append("Recognized Characters (use these canonical names and genders when visual cues match):")
        for c in chars:
            name = c.get("name", "")
            gender = c.get("gender", "unknown")
            role = c.get("role", "")
            cues = c.get("visual_cues", "")
            pronouns = c.get("pronouns", "")
            lines.append(f"  • {name} [{gender}, {pronouns}, {role}]: {cues}")

    lines.append("CRITICAL: When these recognized characters appear, use their canonical names for speaker and visual descriptions. Do NOT confuse festival costumes/props with real monsters.")
    lines.append("-------------------------------------------\n")
    return "\n".join(lines)


def format_for_narrator(bible):
    """Format the Series Bible into a prompt block for narrate.py."""
    if not bible:
        return ""
    lines = [
        "\n--- SERIES BIBLE & CANONICAL CAST ---",
        f"Series: {bible.get("canonical_title", "Unknown")}",
        f"World Setting: {bible.get("world_setting", {}).get("universe", "")} — {bible.get("world_setting", {}).get("premise", "")}",
    ]
    rule = bible.get("world_setting", {}).get("costume_vs_monster_rule")
    if rule:
        lines.append(f"World & Prop Rule: {rule}")

    chars = bible.get("characters", [])
    if chars:
        lines.append("Key Cast (refer to characters by their real names and correct pronouns):")
        for c in chars:
            name = c.get("name", "")
            role = c.get("role", "")
            pronouns = c.get("pronouns", "")
            aliases = ", ".join(c.get("aliases", []))
            lines.append(f"  • {name} ({role}, {pronouns}) [Aliases: {aliases}]")

    lines.append("Rules: Refer to characters using their canonical names from this bible. Never confuse festival costumes/lion puppets with real beasts/golems.")
    lines.append("-------------------------------------\n")
    return "\n".join(lines)


def format_for_seo(bible):
    """Format the Series Bible metadata for seo.py."""
    if not bible:
        return {}
    chars = [c.get("name") for c in bible.get("characters", []) if c.get("name")]
    return {
        "series_id": bible.get("series_id"),
        "canonical_title": bible.get("canonical_title"),
        "aliases": bible.get("aliases", []),
        "characters": chars,
        "core_hook": bible.get("seo_metadata", {}).get("core_hook", ""),
        "keywords": bible.get("seo_metadata", {}).get("high_volume_keywords", []),
        "hashtags": bible.get("seo_metadata", {}).get("hashtags", []),
        "universe": bible.get("world_setting", {}).get("universe", ""),
        "premise": bible.get("world_setting", {}).get("premise", ""),
    }
