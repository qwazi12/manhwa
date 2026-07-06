"""
Generate narration FROM panels, in the style of a reference script, instead of
matching independently-written narration onto panels after the fact.

Why this exists: the matcher aligns beats to panels after both already exist,
so any mismatch between "what the narrator says" and "what's on screen" has to
be patched after the fact (junk filtering, DP alignment, hold penalties). If
narration is instead WRITTEN from the panels in the first place, each sentence
is grounded in a specific panel by construction — the alignment problem is
solved at the source, not papered over downstream.

Pipeline:
  1. Load descriptions.json, drop junk/blank panels (matcher.is_junk_panel).
  2. Group consecutive kept panels into scene chunks (continuity heuristic).
  3. For each scene, ask Gemini to narrate ONLY what's shown, in the style of
     STYLE_ANCHOR (a few sentences quoted from the user's reference script).
  4. Concatenate scene narrations into one flowing script.

Style rules enforced in the prompt (from the reference sample):
  - third-person omniscient, reported speech, NO quotation marks
  - character references rotate (the protagonist / the guy / his name / his
    title) rather than repeating one name every sentence
  - short, sequential, one-action-or-thought-per-sentence
  - no scene headers, no markdown, no metaphor/embellishment
  - literal: describe only what the panels show — never invent plot, lore, or
    backstory the panels don't depict
"""

import json
import os
import re
import sys

import matcher

HERE = os.path.dirname(os.path.abspath(__file__))
DESCRIPTIONS_PATH = os.path.join(HERE, "..", "panel-describe", "descriptions.json")

# A handful of sentences from the user's own reference script, used ONLY as a
# local style anchor inside the prompt (never shown to the end viewer) — not
# copied into any output. Chosen to demonstrate: reported speech (no quotes),
# rotating character reference, and short sequential sentences.
STYLE_ANCHOR = """\
He raised himself from the ground on his right hand and cursed. It seemed to \
him that the strength from under his feet was disappearing. Ash looked up. \
He now leaned on two hands and cursed again. The boy stood up to his full \
height. He saw many figures and robes in front of him and sadly realized \
that they had calculated everything from the very beginning. One of his \
enemies who was standing on the highest branch of a tree noted that Prince \
Ash had reached here in half an hour. His comrades laughed and said that \
they had barely waited for the protagonist."""

NAME_HINT = "protagonist / the guy / the boy / the prince (rotate naturally; do not repeat one name every sentence)"


def load_panels(descriptions_path=None):
    with open(descriptions_path or DESCRIPTIONS_PATH, encoding="utf-8") as f:
        panels = json.load(f)
    panels.sort(key=lambda r: _natural_key(r["panel_id"]))
    return [p for p in panels if p.get("ok", True) and not matcher.is_junk_panel(p)]


def _natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


# Hard scene-break signals — checked BEFORE the lexical-continuity fallback,
# so a lexically-similar pair (e.g. two panels in the same room) still splits
# when one of these fires. Each is a cheap regex proxy for a real signal:
_REACTION_RE = re.compile(
    r"\b(shock(ed|ing)?|gasp|stunned|wide[- ]eyed|horror|horrified|"
    r"stares? (blankly|in disbelief)|recoils?|flinch(es|ed)?|"
    r"speechless|taken aback)\b", re.I)
_EFFECT_RE = re.compile(
    r"\b(flash(es|ed)?|explosion|explodes?|burst(s|ing)?|blast|glow(s|ing)?|"
    r"sudden(ly)?|reveal(s|ed|ing)?|lightning strikes?)\b", re.I)
_LOCATION_RE = re.compile(
    r"\b(room|forest|street|palace|castle|mountain|cliff|field|building|"
    r"academy|square|kitchen|bath(?:house)?|throne|chamber|dorm|courtyard|"
    r"battlefield|clearing|estate|corridor|hallway)\b", re.I)


def _panel_text(p):
    return f"{p.get('visual_description','')} {p.get('ocr_text','')}"


def _location_tokens(p):
    return set(m.lower() for m in _LOCATION_RE.findall(p.get("visual_description", "")))


def _forces_new_scene(prev, cur):
    """True if a hard signal requires a new scene regardless of lexical
    similarity to the previous panel. Each check is a cheap proxy for a real
    editorial cue — see the docstring on group_into_scenes for what each maps
    to; this is a rule-based stand-in for real shot/beat-boundary detection,
    not a claim of scene understanding."""
    prev_ocr = (prev.get("ocr_text") or "").strip()
    cur_ocr = (cur.get("ocr_text") or "").strip()
    cur_desc = cur.get("visual_description", "")

    # sudden effect / flash / reveal always starts its own beat — these are
    # high-impact single-panel moments in the source material, never a
    # continuation of the preceding action
    if _EFFECT_RE.search(cur_desc):
        return True
    # reaction panel immediately after a panel with different/no reaction
    # framing — a shocked/wide-eyed beat is its own reaction shot, not a
    # continuation of what caused it
    if _REACTION_RE.search(cur_desc) and not _REACTION_RE.search(
            prev.get("visual_description", "")):
        return True
    # dialogue/speaker shift: silence -> speech or speech -> silence is a
    # strong signal the "beat" changed even if the visual is similar
    if bool(prev_ocr) != bool(cur_ocr):
        return True
    # new location: this panel names a place the previous one didn't
    prev_locs, cur_locs = _location_tokens(prev), _location_tokens(cur)
    if cur_locs and prev_locs and not (cur_locs & prev_locs):
        return True
    return False


def group_into_scenes(panels, max_group=4, sim_threshold=0.12):
    """Chunk consecutive panels into scene groups.

    Two layers, checked in order for each new panel:
      1. HARD SIGNALS (_forces_new_scene): sudden effect/flash/reveal, a
         reaction beat following non-reaction art, a dialogue/silence
         speaker-shift, or a location change. Any of these splits the scene
         regardless of lexical similarity — a confrontation moving to a new
         phase, or a reaction shot after an action beat, must not be fused
         into one narrated moment just because the wording overlaps.
      2. LEXICAL CONTINUITY (fallback): if no hard signal fires, panels stay
         together while their descriptions still share enough content words
         (same characters/setting still on screen).

    This is a simple, dependency-light stand-in for real shot-boundary
    detection — good enough for a draft grouping, same spirit as the
    panel-splitter's own density heuristics. Not lexical-only, per review.
    """
    if not panels:
        return []
    groups = [[panels[0]]]
    for prev, cur in zip(panels, panels[1:]):
        hard_break = _forces_new_scene(prev, cur)
        sim = matcher._lexical_sim(_panel_text(prev), _panel_text(cur))
        if not hard_break and sim >= sim_threshold and len(groups[-1]) < max_group:
            groups[-1].append(cur)
        else:
            groups.append([cur])
    return groups


def build_prompt(scene_panels):
    lines = []
    for i, p in enumerate(scene_panels, 1):
        desc = p.get("visual_description", "").strip()
        ocr = p.get("ocr_text", "").strip()
        entry = f"Panel {i}: {desc}"
        if ocr:
            entry += f'\n  Dialogue/text visible in this panel: "{ocr}"'
        lines.append(entry)
    panel_block = "\n".join(lines)

    return f"""You are a recap narrator retelling what happens in this scene, \
the way someone would summarize a story to a friend who hasn't read it — not \
an art critic describing a comic panel.

VOICE EXAMPLE (match this voice exactly — sentence rhythm, reported speech, \
tone — do NOT copy its content):
{STYLE_ANCHOR}

WRITE (mandatory):
- Recap-narrator voice: third-person, past tense, smooth connective \
storytelling — sentences link into a flowing account, not a list of frames.
- Narrate EVENTS, ACTIONS, REACTIONS, INTENTIONS, and CONSEQUENCES: what a \
character DID, what happened TO them, what they WANTED, and what RESULTED —
not what the panel shows on the page.
- Convert all dialogue/text below into REPORTED narration, never quoted \
speech and never quotation marks (panel text "I can't move" becomes: he said \
that he could not move anymore).
- Refer to the character using: {NAME_HINT}

DO NOT WRITE (banned regardless of what the panel description mentions):
- Drawing technique or composition: speed lines, motion lines, panel \
framing, camera angle, close-up/wide-shot, "the panel shows", art style.
- Physical appearance UNLESS it is the plot event itself: hair color, \
clothing details, eye color, etc. are almost always noise — omit them. \
(Exception: if a wound, a torn garment, or a visible injury IS the story \
event — e.g. "his arm was bleeding" — narrate that, because it's a \
consequence, not a costume description.)
- Invented names, lore, motives, or backstory. Only state a motive, name, or \
fact if it is directly supported by the OCR/dialogue text or the visible \
action — never infer or guess at meaning the panels don't support.

Prefer STORY MEANING over visual captioning: if a panel shows a character \
stumbling and gritting their teeth, narrate that they struggled to keep \
going and refused to give up — not that "their face is shown in close-up \
with clenched teeth."

PANELS (in order, all part of the same continuous moment):
{panel_block}

Write the narration for this moment now. Plain text only, no preamble, no \
labels, just the narration sentences."""


def narrate_scene(scene_panels, model="gemini-3.5-flash"):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY not set")
    from google import genai
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model, contents=build_prompt(scene_panels))
    return (resp.text or "").strip()


def generate_narration(panels, model="gemini-3.5-flash", verbose=True):
    """Run the full pipeline over `panels` (already filtered/ordered) and
    return (full_script_text, [(scene_panels, scene_text), ...])."""
    scenes = group_into_scenes(panels)
    results = []
    for i, scene in enumerate(scenes, 1):
        ids = [p["panel_id"] for p in scene]
        if verbose:
            print(f"[scene {i}/{len(scenes)}] {len(scene)} panel(s): {ids}",
                  file=sys.stderr)
        text = narrate_scene(scene, model)
        results.append((scene, text))
    full_script = "\n\n".join(text for _, text in results)
    return full_script, results


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Generate narration from panels")
    ap.add_argument("--limit-panels", type=int, default=10,
                     help="only use the first N (non-junk) panels — for a cheap style-check sample")
    ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--descriptions", help="override descriptions.json path (e.g. a fresh chapter's subset)")
    args = ap.parse_args()

    panels = load_panels(args.descriptions)
    if args.limit_panels:
        panels = panels[:args.limit_panels]

    print(f"Using {len(panels)} non-junk panels: {[p['panel_id'] for p in panels]}",
          file=sys.stderr)
    script, results = generate_narration(panels, model=args.model)

    print("\n" + "=" * 60)
    print("SCENE-BY-SCENE BREAKDOWN")
    print("=" * 60)
    for scene, text in results:
        ids = ", ".join(p["panel_id"] for p in scene)
        print(f"\n--- scene: {ids} ---")
        print(text)

    print("\n" + "=" * 60)
    print("FULL CONCATENATED SCRIPT (this sample)")
    print("=" * 60)
    print(script)
