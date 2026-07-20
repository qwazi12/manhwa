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

# Cost/abuse guardrails (review_ui/usage.py) — optional no-op if unavailable.
_REVIEW_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_ui")
if os.path.isdir(_REVIEW_UI) and _REVIEW_UI not in sys.path:
    sys.path.insert(0, _REVIEW_UI)
try:
    import usage
except ImportError:
    usage = None

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


def call_gemini_rest(model, prompt, api_key):
    """Call Gemini for text generation. Routes to Interactions API for AQ. auth
    keys (gemini-3.5-flash), falls back to generateContent for legacy AIzaSy keys."""
    import json
    import urllib.request
    import ssl

    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl._create_unverified_context()

    if api_key.startswith("AQ."):
        # New Interactions API path (required for gemini-3.5-flash + auth
        # keys). Plain-string `input` is the docs-canonical text-only form.
        import time as _time
        import urllib.error
        url = "https://generativelanguage.googleapis.com/v1beta/interactions"
        payload = {"model": model, "input": prompt}
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}
        last_err = None
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=300, context=context) as response:
                    res = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:400]
                except Exception:
                    pass
                # Retry transient failures; surface everything else WITH the
                # API's own explanation (job db7216d976ce died as an opaque
                # bare "HTTP Error 400" because this body was discarded).
                if e.code in (429, 500, 502, 503) and attempt < 2:
                    last_err = f"{e.code}: {body}"
                    _time.sleep(8 * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"Gemini Interactions API {e.code} (model={model}, "
                    f"prompt={len(prompt)} chars, attempt {attempt+1}): {body}") from e
        else:
            raise RuntimeError(
                f"Gemini Interactions API kept failing after retries: {last_err}")
        # Concatenate ALL model_output text parts — returning only the first
        # part silently truncates long narrations.
        parts = [part["text"]
                 for step in res.get("steps", [])
                 if step.get("type") == "model_output"
                 for part in step.get("content", [])
                 if part.get("type") == "text" and part.get("text")]
        if not parts:
            raise ValueError(
                f"No model_output text in interactions response. Keys: {list(res.keys())}")
        return "".join(parts).strip()
    else:
        # Legacy generateContent path (AIzaSy standard keys)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120, context=context) as response:
            res = json.loads(response.read().decode("utf-8"))
        try:
            return res["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as e:
            print(f"Gemini REST response error: {res}", file=sys.stderr)
            raise e


def merge_into_units(scenes, max_panels=10):
    """Merge small scene groups into NARRATION UNITS (A1 density fix).

    The style contract alone cannot produce story density when each Gemini
    call sees a 1-4 panel scene: every call still writes a paragraph, and 60
    calls write an hour of narration (live-eval finding: 19k words). Larger
    units give the model room to compress runs of panels into single
    sentences, and the per-unit word budget makes the target explicit."""
    units, cur = [], []
    for sc in scenes:
        if cur and len(cur) + len(sc) > max_panels:
            units.append(cur)
            cur = list(sc)
        else:
            cur.extend(sc)
    if cur:
        units.append(cur)
    return units


def dialogue_lines(panels):
    """Count distinct dialogue utterances across panels (describe.py joins
    bubbles with ' / ')."""
    n = 0
    for p in panels:
        n += sum(1 for part in p.get("ocr_text", "").split("/") if part.strip())
    return n


def word_budget(n_panels, n_dialogue=0):
    """Per-unit narration budget: ~12 words/panel PLUS ~7 words per dialogue
    utterance, floor 40, cap 220. T4 (Session 23): dialogue-heavy pages EARN
    proportionally more narration — the flat panel-only budget compressed an
    8-bubble page into one sentence, which read as a summary, not a story."""
    return max(40, min(220, 12 * n_panels + 7 * n_dialogue))


def build_prompt(scene_panels, global_beatsheet=None, budget=None):
    lines = []
    for i, p in enumerate(scene_panels, 1):
        desc = p.get("visual_description", "").strip()
        ocr = p.get("ocr_text", "").strip()
        entry = f"Panel {i} (ID: {p.get('panel_id')}): {desc}"
        if ocr:
            entry += f'\n  Dialogue/text visible in this panel: "{ocr}"'
        lines.append(entry)
    panel_block = "\n".join(lines)

    global_context_block = ""
    if global_beatsheet:
        global_context_block = f"""
--- GLOBAL CHAPTER SUMMARY & PACING DIRECTIVE ---
Here is the overall storyline outline and emotional pacing flow for the entire chapter. Use this to ensure continuity of plot, character motivations, and natural transitions across scene boundaries:
{global_beatsheet}
------------------------------------------------
"""

    return f"""You are a master comic-recap narrator writing the voiceover script for a recap video. You retell the chapter as one smooth story — you are NOT captioning images.

{global_context_block}

STYLE CONTRACT (every rule mandatory):
1. Third-person, past tense, story-first: retell events as one flowing narrative.
2. Every sentence must carry an EVENT, REACTION, REALIZATION, INTENTION, or CONSEQUENCE. A sentence that only describes how something looks gets cut, or its detail folded into an action.
2b. DIALOGUE FIDELITY: every meaningful exchange in the panels gets its own reported-speech sentence — collapsing a whole conversation into one summary line is a contract violation. Only trivial fillers (grunts, one-word reactions, repeated shouts) may fold into a neighbouring sentence.
3. Convert all visible dialogue/text into reported narration — never quotation marks (panel text "Who are you?" becomes: he demanded to know who the stranger was).
4. No panel/framing/camera/art language, ever: never "the panel/image/frame shows", "close-up", "speed lines", "we see" — and NEVER the word "camera" in any form.
4b. Each scene continues where the previous narration left off — never re-introduce or re-tell events already covered (the chapter summary shows you where you are in the story).
5. Appearance, clothing, and setting details appear ONLY when plot-relevant or atmosphere-setting — one economical touch, not an inventory.
6. Enrichment policy: infer motive, emotion, and subtext when the art or dialogue clearly implies it; smooth small gaps the way a recap narrator who knows the story would. NEVER invent names, numbers, backstory, or events without support in the panels.
7. DENSITY IS EDITORIAL, NOT MECHANICAL: narrate the story, not the panels. A run of panels showing one continuous action gets ONE sentence. A filler/transition panel earns ZERO sentences. Only a true story peak earns 2-3 sentences. Never average "sentences per panel".

VOICE ANCHOR (match this cadence — sentences that move, reported speech, zero scenery padding):
- "The war with the labyrinth had raged for decades — humanity against the things that boiled up from below, soldiers emptying their guns into biomechanical horrors while monsters charged in roaring hordes."
- "The plea did no good. The club came down anyway, and the man who had once been the ninth-ranked warrior alive could do nothing but shield his head and take it."
- "Somewhere in that laughter, something in him finally settled. He had needed the reminder, he thought, that he was no longer the man from those glory days."

OUTPUT: only the raw storytelling text — no preamble, labels, or panel references.

PANELS (in order):
{panel_block}

STRICT LENGTH BUDGET: write AT MOST {budget or word_budget(len(scene_panels), dialogue_lines(scene_panels))} words for this ENTIRE scene — count them. Compress ruthlessly: pick only the events that move the story, fold the rest into them or skip them outright.

Write the narration for this scene now, at story density (far fewer sentences than panels):"""


def generate_global_beatsheet(panels, model="gemini-3.5-flash"):
    """First pass of the narration pipeline: compiles all panel metadata for the chapter
    and generates a cohesive, chronological plot and pacing outline.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY not set")
        
    lines = []
    for i, p in enumerate(panels, 1):
        desc = p.get("visual_description", "").strip()
        ocr = p.get("ocr_text", "").strip()
        entry = f"Panel {i} ({p.get('panel_id')}): {desc}"
        if ocr:
            entry += f" [Dialogue: {ocr}]"
        lines.append(entry)
    chapter_block = "\n".join(lines)
    
    prompt = f"""You are a master story editor preparing a chapter-wide outline and pacing guide for a comic-recap video script. 
Analyze the full sequence of panels below for this chapter. 

Develop a clear, cohesive, chronological chapter story outline (a "beat sheet"). 
Your outline must:
1. Identify all key characters, their roles, names, and physical descriptions (e.g., Ash the long-haired barefoot boy, James the bodyguard, Weiss the old advisor).
2. Trace the clear flow of plot points, scene transitions, action peaks (fights/explosions), and quiet lore/worldbuilding exposition.
3. Define the narrative tone progression (e.g., starts in high tension/flight, shifts to mystery/lore, ends in determination).
4. Summarize the overall narrative arc so that scene-by-scene script generators know how each local moment fits into the larger story.
5. Mark PACING explicitly: name the story peaks that deserve detailed narration, and name the filler/transition stretches (repeated action panels, establishing shots, promo/credits cards) that the narration should compress to one sentence or pass over entirely. The final video should feel like a tight 6-10 minute recap, not a panel-by-panel caption track.

PANELS SEQUENCE:
{chapter_block}

Write the story outline and pacing beat sheet now. Plain text only, no formatting fluff."""

    def _call():
        return call_gemini_rest(model, prompt, api_key)

    if usage:
        with usage.gate("gemini", 1, model=model):
            return _call()
    else:
        return _call()


def narrate_scene(scene_panels, model="gemini-3.5-flash", global_beatsheet=None):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY not set")

    def _call():
        return call_gemini_rest(model, build_prompt(scene_panels, global_beatsheet), api_key)

    if usage:
        with usage.gate("gemini", 1, model=model):
            return _call()
    else:
        return _call()


MAX_REVISED_UNITS = 3   # A3: cap revision calls per chapter


def critique_units(results, model, api_key):
    """A3 pass 1: ONE reviewer call over the whole draft. Returns issue list
    [{"unit": int, "type": ..., "problem": ..., "fix": ...}]. Types:
    hallucination | misorder | missed_beat | style_violation."""
    lines = []
    for i, (scene_panels, text) in enumerate(results):
        pids = ", ".join(p["panel_id"] for p in scene_panels)
        facts = " | ".join(
            f"{p.get('visual_description','')[:110]}"
            + (f" [text: {p.get('ocr_text','')[:60]}]" if p.get("ocr_text") else "")
            for p in scene_panels)
        lines.append(f"UNIT {i} (panels: {pids})\nPANEL FACTS: {facts}\nDRAFT: {text}")
    prompt = f"""You are a fact-checking script editor for a comic-recap narration. For each UNIT below, compare the DRAFT narration against the PANEL FACTS it was written from.

Report ONLY real problems, as a JSON array (empty array if none):
[{{"unit": <int>, "type": "hallucination|misorder|missed_beat|style_violation", "problem": "<what is wrong>", "fix": "<how to fix in one sentence>"}}]

- hallucination: names, numbers, events, or motives with NO support in the panel facts
- misorder: events narrated in a different order than the panels
- missed_beat: a clearly major story event in the facts that the draft skips entirely
- style_violation: quoted dialogue, present tense, or ANY mention of the camera/panel/image/frame ("the camera pans", "the panel shows")
- redundancy: the unit re-tells events an EARLIER unit already narrated (recaps must never repeat themselves)

Output the JSON array only.

{chr(10).join(lines)}"""
    if usage:
        with usage.gate("gemini", 1, model=model):
            raw = call_gemini_rest(model, prompt, api_key)
    else:
        raw = call_gemini_rest(model, prompt, api_key)
    m = re.search(r"\[.*\]", raw or "", re.S)
    try:
        issues = json.loads(m.group(0)) if m else []
        return [i for i in issues if isinstance(i, dict) and isinstance(i.get("unit"), int)]
    except json.JSONDecodeError:
        print(f"critique: unparseable reviewer output, skipping revision", file=sys.stderr)
        return []


def revise_unit(scene_panels, draft, unit_issues, model, global_beatsheet, api_key):
    """A3 pass 2: regenerate ONE flagged unit with the reviewer's notes
    appended — provenance (unit -> panels) is preserved by construction."""
    notes = "\n".join(f"- {i['type']}: {i['problem']} FIX: {i['fix']}"
                      for i in unit_issues)
    prompt = (build_prompt(scene_panels, global_beatsheet)
              + f"\n\nEDITOR NOTES on the previous draft (you MUST fix these):\n{notes}"
              + f"\n\nPREVIOUS DRAFT:\n{draft}\n\nRewrite the narration for this scene now:")
    if usage:
        with usage.gate("gemini", 1, model=model):
            return call_gemini_rest(model, prompt, api_key)
    return call_gemini_rest(model, prompt, api_key)


def generate_narration(panels, model="gemini-3.5-flash", verbose=True,
                       progress_cb=None):
    """Run the full pipeline over `panels` (already filtered/ordered) and
    return (full_script_text, [(scene_panels, scene_text), ...])."""
    # Pass 1: Generate global pacing beatsheet for the entire chapter
    if verbose:
        print(f"Generating global pacing beatsheet using {model}...", file=sys.stderr)
    global_beatsheet = generate_global_beatsheet(panels, model)
    
    # Pass 2: scene grouping -> NARRATION UNITS (merged scenes, <=10 panels)
    # with a hard per-unit word budget — the structural density control.
    scenes = merge_into_units(group_into_scenes(panels))
    results = []
    for i, scene in enumerate(scenes, 1):
        ids = [p["panel_id"] for p in scene]
        if verbose:
            print(f"[unit {i}/{len(scenes)}] {len(scene)} panel(s): {ids}",
                  file=sys.stderr)
        if progress_cb:
            progress_cb(i, len(scenes), "draft")
        text = narrate_scene(scene, model, global_beatsheet)
        results.append((scene, text))

    # A3: critique-and-revise — one reviewer call over the whole draft, then
    # regenerate at most MAX_REVISED_UNITS flagged units with editor notes.
    api_key = os.environ.get("GEMINI_API_KEY")
    try:
        if progress_cb:
            progress_cb(len(scenes), len(scenes), "review")
        issues = critique_units(results, model, api_key)
        if issues:
            by_unit = {}
            for it in issues:
                by_unit.setdefault(it["unit"], []).append(it)
            worst = sorted(by_unit, key=lambda u: -len(by_unit[u]))[:MAX_REVISED_UNITS]
            for u in worst:
                if 0 <= u < len(results):
                    scene, draft = results[u]
                    fixed = revise_unit(scene, draft, by_unit[u], model,
                                        global_beatsheet, api_key)
                    if fixed and fixed.strip():
                        results[u] = (scene, fixed.strip())
            print(f"critique: {len(issues)} issue(s), revised "
                  f"{min(len(by_unit), MAX_REVISED_UNITS)} unit(s)", file=sys.stderr)
    except (usage.UsageCapExceeded if usage else ()) :
        raise
    except Exception as e:
        # The review pass is a quality net, never a job-killer.
        print(f"critique pass skipped: {e}", file=sys.stderr)

    full_script = "\n\n".join(text for _, text in results)
    return full_script, results


def provenance(results):
    """Structured script (B1): which panels each scene's text was written
    about. Persisted as script.json so downstream stages never have to
    reverse-engineer the panel<->narration mapping the narrator already knew."""
    return [{"scene_id": i,
             "panel_ids": [p["panel_id"] for p in scene_panels],
             "text": text}
            for i, (scene_panels, text) in enumerate(results)]


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
