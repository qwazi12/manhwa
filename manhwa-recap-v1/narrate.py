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


def load_panels():
    with open(DESCRIPTIONS_PATH, encoding="utf-8") as f:
        panels = json.load(f)
    panels.sort(key=lambda r: _natural_key(r["panel_id"]))
    return [p for p in panels if p.get("ok", True) and not matcher.is_junk_panel(p)]


def _natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def group_into_scenes(panels, max_group=4, sim_threshold=0.12):
    """Chunk consecutive panels into scene groups by lexical continuity.

    Consecutive panels stay in the same scene while their descriptions share
    enough content words (same characters/setting still on screen); a bigger
    jump starts a new scene. This is a simple, dependency-light stand-in for
    real shot-boundary detection — good enough for a draft grouping, same
    spirit as the panel-splitter's own heuristics.
    """
    if not panels:
        return []
    groups = [[panels[0]]]
    for prev, cur in zip(panels, panels[1:]):
        prev_text = f"{prev.get('visual_description','')} {prev.get('ocr_text','')}"
        cur_text = f"{cur.get('visual_description','')} {cur.get('ocr_text','')}"
        sim = matcher._lexical_sim(prev_text, cur_text)
        if sim >= sim_threshold and len(groups[-1]) < max_group:
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

    return f"""You are narrating a manhwa recap in a specific established style.

STYLE EXAMPLE (match this voice exactly — sentence rhythm, reported speech, \
tone — do NOT copy its content):
{STYLE_ANCHOR}

STYLE RULES (mandatory):
- Third-person omniscient, reported speech only. NEVER use quotation marks. \
Convert any dialogue shown below into reported speech (e.g. panel text \
"I can't move" becomes: He said that he could not move anymore).
- Refer to the character using: {NAME_HINT}
- Short, sequential sentences — one action or thought per sentence.
- No scene headers, no markdown, no metaphors, no flowery description.
- State emotions factually (he was scared / he screamed / he was shocked), \
not poetically.
- Describe ONLY what is visibly shown in the panels below. Do NOT invent \
plot points, names, lore, or backstory that isn't depicted. If a panel is \
ambiguous, describe it plainly rather than guessing at meaning.

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
    args = ap.parse_args()

    panels = load_panels()
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
