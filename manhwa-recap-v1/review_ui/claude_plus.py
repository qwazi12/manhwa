"""Claude+ — the lab's upgraded stage contracts.

The lab's first version was under-specified in exactly the places production is
strongest, and production is strongest because every stage is checked by
something that is not the model. This module ports that discipline across and
then improves on it where production's own heuristics are the weak part.

WHAT IS INHERITED FROM PRODUCTION (because production is right):
  - a hard 50-word description cap, a forced action-verb opening, banned
    generic openers
  - a computed per-scene word budget instead of "write some narration"
  - "density is editorial, not mechanical", dialogue fidelity, reported speech,
    and the ban on panel/camera/frame language
  - a critique pass with typed issues, then targeted regeneration of only the
    units that were flagged
  - crop chosen AFTER the script and the placement exist, driven by the line

WHAT IS IMPROVED BEYOND PRODUCTION (because these are its known weak points):
  - OCR is no longer blindly the highest-weighted signal. The reader reports how
    much it trusts what it read, and a low-confidence read is scored down
    instead of dragging a line onto the wrong panel.
  - The word budget is no longer a flat cap. A scene that is genuinely dense —
    many real exchanges, a reveal, a climax — can EARN more, and every expansion
    is logged with its reason so the allowance cannot quietly become a leak.
  - The crop trigger is no longer a keyword list. Claude is asked what the line
    is actually about and whether a crop would serve it, so an emotional beat
    with no keyword in it can still get its close-up.

Nothing here writes production files. Isolation, checkpointing and cost gating
are unchanged — every call still goes through usage.gate('claude', ...).
"""

import json
import os
import re

import usage
import validator
from claude_pipeline import (_Tally, _clean_crop, _image_block, _write, read,
                             out_dir, MODEL, EFFORT, PANELS_PER_CALL,
                             SCRIPT_CHUNK)

# ------------------------------------------------------------------ tuning
# Production's budget, ported verbatim: ~12 words per panel plus ~7 per
# dialogue utterance, floored and capped. Dialogue-heavy pages earn more,
# because a flat panel-only budget once compressed an 8-bubble page into one
# sentence.
WORDS_PER_PANEL = float(os.environ.get("PLUS_WORDS_PER_PANEL", 12))
WORDS_PER_DIALOGUE = float(os.environ.get("PLUS_WORDS_PER_DIALOGUE", 7))
BUDGET_FLOOR = int(os.environ.get("PLUS_BUDGET_FLOOR", 40))
BUDGET_CAP = int(os.environ.get("PLUS_BUDGET_CAP", 220))

# THE DENSE-SCENE ALLOWANCE — the improvement on production's flat cap.
# A scene that is structurally important or unusually full of real exchanges
# may exceed the cap, up to this multiple and this hard ceiling. Every grant is
# logged; an allowance nobody can audit is just a higher cap.
DENSE_MULTIPLIER = float(os.environ.get("PLUS_DENSE_MULTIPLIER", 1.5))
DENSE_HARD_CAP = int(os.environ.get("PLUS_DENSE_HARD_CAP", 320))

# THE BUDGET IS A RANGE, NOT A CEILING. Measured on the Overgeared run: large
# scenes spent 21-36% of their budget and narration was flat at ~40 words per
# unit regardless of scene size, because "compress ruthlessly" drives to the
# floor while the budget only caps the top. Density fell 12.49 -> 3.98 words
# per panel. Raising the ceiling therefore does nothing — the ceiling was never
# approached. A floor is what was missing.
TARGET_MIN_FRAC = float(os.environ.get("PLUS_TARGET_MIN", 0.50))
TARGET_MAX_FRAC = float(os.environ.get("PLUS_TARGET_MAX", 0.90))
# A scene only counts as content-rich — and so only earns an under-spend flag —
# if it actually has something to say.
RICH_MIN_PANELS = int(os.environ.get("PLUS_RICH_MIN_PANELS", 4))
RICH_MIN_DIALOGUE = int(os.environ.get("PLUS_RICH_MIN_DIALOGUE", 2))

MAX_REVISIONS = int(os.environ.get("PLUS_MAX_REVISIONS", 12))


class PlusError(RuntimeError):
    """A condition the operator must see."""


# ============================================================ STAGE: READ
DESCRIBE_SYSTEM = """\
You are reading single panels cut from a manhwa (a Korean comic read top to \
bottom). For each panel image you are given, return what an automated recap \
pipeline needs to work from.

OCR (`ocr`)
- Transcribe EVERY piece of text visible in the panel exactly as written: \
dialogue, narration boxes, sound effects, signs. Names, curses and sound \
effects all matter — they are the sharpest signal for matching a line to a \
panel later.
- Join separate text blocks with " / " in reading order.
- Keep punctuation-only bubbles as they are ("!!!", "ACK!!").
- Empty string if the panel genuinely has no text.
- Do NOT transcribe scanlation watermarks, site names, Discord links or \
translator credits as though they were story text. Set `is_credits` true \
instead and still report what you saw.

`ocr_confidence` (0.0-1.0) — how confident you are that your transcription is \
correct AND belongs to this panel. Lower it when text is small, blurred, \
stylised, partly cut off, overlapping a panel border, or bleeding in from a \
neighbouring panel. This number is used to decide how much to trust the text \
later, so an honest low score is worth far more than a confident wrong one.

DESCRIPTION (`description`)
- MAXIMUM 50 WORDS. Count them.
- If something is HAPPENING in the panel, open with that action as a verb \
phrase — not with the subject, and not with a framing word. \
Right: "Tumbling down a rocky slope, the dark-haired boy claws at the dirt." \
Wrong: "A boy falls down a slope." \
Wrong: "A panel depicting a boy falling."
- If the panel is a title card, a credits block, a narration box or a pure \
sound effect, there is NO action to lead with. Describe it plainly and \
naturally instead — do not manufacture a verb phrase for a static image.
- NEVER open with "a scene showing", "a panel depicting", "the image features", \
or any equivalent.
- After the action: who is in frame (name if it is visible), the setting, and \
the shot type.
- Write it so someone who cannot see the image could tell this panel apart from \
every other panel in the chapter.
- Describe ONLY what is visibly in THIS panel. Do not invent names, motives, \
plot or backstory.

`desc_confidence` (0.0-1.0) — how confident you are that your description is \
specific and correct. Lower it when the art is ambiguous, the subject is \
unclear, or the best you can honestly write would fit many panels.

Also return:
- `is_credits` — true for a scanlation credits, watermark or advertising block.
- `subject_type` — one of "character", "environment", "action", "text", \
"effect", "credits".
- `importance` — 1 to 5, how much this panel matters to the chapter's story.
- `needs_review` — true when a human should look at this panel before trusting \
it (text you could not read, a panel you could not interpret, a likely bad cut).
- `focus_hint` — ADVISORY ONLY. If part of the panel obviously carries the \
subject, give it as [x0, y0, x1, y1] fractions; otherwise [0, 0, 1, 1]. The \
real crop is decided later, once the narration exists, so do not agonise here.

Answer for EVERY panel you are given, using the panel numbers given."""

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "ocr": {"type": "string"},
                    "ocr_confidence": {"type": "number"},
                    "description": {"type": "string"},
                    "desc_confidence": {"type": "number"},
                    "is_credits": {"type": "boolean"},
                    "subject_type": {"type": "string"},
                    "importance": {"type": "integer"},
                    "needs_review": {"type": "boolean"},
                    "focus_hint": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["n", "ocr", "ocr_confidence", "description",
                             "desc_confidence", "is_credits", "subject_type",
                             "importance", "needs_review", "focus_hint"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["panels"],
    "additionalProperties": False,
}

# Openers the contract bans. Checked in code, not just asked for in the prompt —
# an instruction nothing verifies is a wish.
_BANNED_OPENERS = re.compile(
    r"^\s*(a |an |the )?(scene|panel|image|picture|frame|shot|illustration|"
    r"close[- ]?up|view)\b|^\s*(this|it) (is|shows|depicts)|"
    r"^\s*(we see|there (is|are))\b", re.IGNORECASE)

DESC_WORD_CAP = int(os.environ.get("PLUS_DESC_WORD_CAP", 50))


def _clamp01(v, default=1.0):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


# Panels with no action in them. The action-verb rule does not apply to these —
# see audit_description.
STATIC_SUBJECTS = ("text", "credits", "effect")


def audit_description(text, subject_type=None, is_credits=False):
    """Return the contract violations in one description.

    The prompt states the rules; this checks them. Violations do not throw —
    they are recorded on the panel and reported, because a slightly long
    description is still usable and a pipeline that hard-failed on one would be
    worse than one that flags it.

    THE ACTION-VERB RULE IS NOT APPLIED TO STATIC PANELS. Forcing it on a
    credits card or a text-only panel produced exactly the contortions you would
    expect on the Overgeared run: "Displaying a chapter title card…" on a
    credits page, and "Declaring in a spiky burst bubble, the line floats
    over…" on a narration box — a dangling participle, since the line is not
    the thing declaring. A title card HAS no action, so demanding an action verb
    makes the description worse, not better. Production has the same rule but
    filters these panels out before they reach it.
    """
    out = []
    t = (text or "").strip()
    if not t:
        return ["empty"]
    n = len(t.split())
    if n > DESC_WORD_CAP:
        out.append(f"over_word_cap:{n}")
    if _BANNED_OPENERS.match(t):
        out.append("banned_opener")
    static = is_credits or (subject_type or "").lower() in STATIC_SUBJECTS
    if not static:
        # "MUST open with a verb phrase" — the cheap, reliable proxy is an -ing
        # opener, the form production's examples all use. Flagged, not
        # rejected: English has other valid action openings and a false reject
        # would be worse than a note.
        first = t.split()[0].lower().strip(",.")
        if not first.endswith("ing"):
            out.append("weak_opener")
    return out


def describe_plus(pdir, panels, model=None, progress=None, on_batch=None,
                  skip=frozenset(), tally=None):
    """Read every panel under the ported contract, with confidence reporting.

    Checkpointed per batch and resumable, exactly as before — a restart skips
    panels already read rather than paying for them twice.

    `tally` may be supplied by the caller so it can read the running cost while
    this is still going. Without that, a stage that dies part-way reports NONE
    of what it spent: the estate-developer run's card read "19 Claude calls ·
    $0.1432" when the read stage had in fact completed ~206 panels, because the
    manifest is only updated when a stage finishes. The usage ledger was right
    the whole time — it gates every call — but the run's own card understated
    its spend by an order of magnitude.
    """
    model = model or MODEL
    client = validator._client()
    tally = tally if tally is not None else _Tally()
    out = []
    todo = [p for p in panels if p["panel_id"] not in skip]
    if progress and skip:
        progress(f"Resuming — {len(skip)} panels already read, {len(todo)} to go")
    batches = [todo[i:i + PANELS_PER_CALL]
               for i in range(0, len(todo), PANELS_PER_CALL)]

    for bi, batch in enumerate(batches, start=1):
        if progress:
            progress(f"Claude reading panels {batch[0]['_n']}–{batch[-1]['_n']} "
                     f"({bi}/{len(batches)})")
        content = []
        for rec in batch:
            img = _image_block(pdir, rec)
            if img is None:
                continue
            content.append({"type": "text", "text": f"PANEL {rec['_n']}:"})
            content.append(img)
        if not content:
            continue
        content.append({"type": "text",
                        "text": "Read each panel above, by its number."})
        resp, meter, cost = validator._call_claude(
            client, model=model, max_tokens=16000,
            system=[{"type": "text", "text": DESCRIBE_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT,
                           "format": {"type": "json_schema",
                                      "schema": DESCRIBE_SCHEMA}},
            messages=[{"role": "user", "content": content}])
        tally.add(meter, cost)

        by_n = {r["_n"]: r for r in batch}
        for item in validator._parse_json_reply(resp).get("panels", []):
            rec = by_n.get(item.get("n"))
            if rec is None:
                continue                     # a panel we did not send is noise
            desc = (item.get("description") or "").strip()
            ocr = (item.get("ocr") or "").strip()
            is_cred = bool(item.get("is_credits"))
            # A panel with nothing at all is only acceptable when it is
            # explicitly a blank or a credits block; otherwise the read failed
            # and must be marked rather than shipped as a silent empty.
            violations = audit_description(
                desc, subject_type=item.get("subject_type"), is_credits=is_cred)
            blank_ok = is_cred or (item.get("subject_type") == "effect")
            out.append({
                "panel_id": rec["panel_id"], "file": rec.get("file"),
                "width": rec.get("width"), "height": rec.get("height"),
                "n": rec["_n"],
                "ocr_text": ocr,
                "ocr_confidence": _clamp01(item.get("ocr_confidence")),
                "visual_description": desc,
                "desc_confidence": _clamp01(item.get("desc_confidence")),
                "is_credits": is_cred,
                "subject_type": item.get("subject_type", ""),
                "importance": int(item.get("importance") or 3),
                "needs_review": bool(item.get("needs_review"))
                                or (not desc and not blank_ok)
                                or "empty" in violations,
                "contract_violations": violations,
                # Advisory only. The real crop is decided after placement.
                "focus_hint": _clean_crop(item.get("focus_hint")),
                "source": "claude+", "ok": bool(desc) or blank_ok,
            })
        if on_batch:
            on_batch(out)

    out.sort(key=lambda r: r["n"])
    return out, tally.stats(
        batches=len(batches), panels=len(out), resumed=len(skip),
        needs_review=sum(1 for d in out if d["needs_review"]),
        over_cap=sum(1 for d in out
                     if any(v.startswith("over_word_cap") for v in d["contract_violations"])),
        banned_openers=sum(1 for d in out
                           if "banned_opener" in d["contract_violations"]),
        low_ocr_conf=sum(1 for d in out if d["ocr_confidence"] < 0.45),
    )


# =================================================== STAGE: CHAPTER MAP
CHAPTER_SYSTEM = """\
You are reading a whole manhwa chapter, panel by panel in reading order, and \
building the map every later stage will be judged against.

Return:
- `premise`: one or two sentences on what this chapter is about.
- `scenes`: consecutive runs of panels forming ONE story movement. Aim for \
FOUR TO SIX PANELS per scene. A scene is not "everything that happens in this \
location" — start a new one at any clear sub-beat: an action shift, a turn in \
the dialogue, a reveal, the aftermath of a reveal, a change of setting, or a \
change of who is present. A ten-panel stretch is almost always two or three \
scenes, not one. Cover every panel; scenes must not overlap. For each, give \
the panel range, a short title, its phase in the chapter arc, and a one-line \
summary.
- `reveals`: points where the audience learns something they did not know — an \
identity, a betrayal, a death, a power. Give the panel where it LANDS. A recap \
that shows a reveal before the line setting it up spoils itself.
- `turns`: points where the emotional register changes.
- `climax_panel`: the panel carrying the chapter's peak, if there is one.
- `compression_risks`: scene numbers that a word budget is likely to flatten — \
scenes carrying several distinct exchanges, or a reveal plus its reaction. \
These are the scenes that will be allowed extra words, so name them honestly \
rather than generously.

Describe only what the panels support. Do not invent plot."""

CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "premise": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer"},
                    "title": {"type": "string"},
                    "first_panel": {"type": "integer"},
                    "last_panel": {"type": "integer"},
                    "phase": {"type": "string", "enum": [
                        "setup", "escalation", "climax", "resolution"]},
                    "summary": {"type": "string"},
                },
                "required": ["scene", "title", "first_panel", "last_panel",
                             "phase", "summary"],
                "additionalProperties": False,
            },
        },
        "reveals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"panel": {"type": "integer"},
                               "what": {"type": "string"}},
                "required": ["panel", "what"], "additionalProperties": False,
            },
        },
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"panel": {"type": "integer"},
                               "turn": {"type": "string"}},
                "required": ["panel", "turn"], "additionalProperties": False,
            },
        },
        "climax_panel": {"type": "integer"},
        "compression_risks": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["premise", "scenes", "reveals", "turns", "climax_panel",
                 "compression_risks"],
    "additionalProperties": False,
}


def chapter_map(descs, model=None, progress=None):
    """One call. Everything downstream is judged against this."""
    model = model or MODEL
    client = validator._client()
    tally = _Tally()
    if progress:
        progress(f"Claude mapping the chapter ({len(descs)} panels)")
    payload = json.dumps(
        [{"panel": d["n"], "shows": (d.get("visual_description") or "")[:220],
          "text": (d.get("ocr_text") or "")[:140],
          "credits": bool(d.get("is_credits"))} for d in descs],
        ensure_ascii=False, indent=1)
    resp, meter, cost = validator._call_claude(
        client, model=model, max_tokens=16000,
        system=[{"type": "text", "text": CHAPTER_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT,
                       "format": {"type": "json_schema",
                                  "schema": CHAPTER_SCHEMA}},
        messages=[{"role": "user", "content": "Map this chapter.\n\n" + payload}])
    tally.add(meter, cost)
    cmap = validator._parse_json_reply(resp)
    scenes = cmap.get("scenes") or []
    if not scenes:
        raise PlusError("Claude returned no scenes for this chapter.")
    return cmap, tally.stats(scenes=len(scenes),
                             reveals=len(cmap.get("reveals") or []))


# ====================================================== STAGE: SCRIPT
def dialogue_lines(panels):
    """Panels carrying a real utterance, not a grunt or a sound effect."""
    n = 0
    for p in panels:
        t = (p.get("ocr_text") or "").strip()
        if len(t.split()) >= 3 and not p.get("is_credits"):
            n += 1
    return n


def word_budget(n_panels, n_dialogue):
    """Production's formula, ported verbatim."""
    return max(BUDGET_FLOOR,
               min(BUDGET_CAP,
                   int(WORDS_PER_PANEL * n_panels + WORDS_PER_DIALOGUE * n_dialogue)))


def scene_budget(scene, panels, cmap):
    """Budget for one scene, with an auditable dense-scene allowance.

    Production stops at the flat cap, which is what flattens a genuinely dense
    scene. Here a scene can EARN more — but only for reasons that are named,
    and every grant is returned so it can be reviewed. An allowance nobody can
    audit is just a higher cap with extra steps.
    """
    n_d = dialogue_lines(panels)
    base = word_budget(len(panels), n_d)
    reasons = []

    if scene.get("scene") in set(cmap.get("compression_risks") or []):
        reasons.append("flagged as a compression risk by the chapter map")
    if scene.get("phase") in ("climax", "escalation"):
        reasons.append(f"{scene.get('phase')} scene")
    climax = cmap.get("climax_panel")
    if climax and scene.get("first_panel", 0) <= climax <= scene.get("last_panel", 0):
        reasons.append("carries the chapter's climax")
    reveal_panels = {r.get("panel") for r in (cmap.get("reveals") or [])}
    if any(scene.get("first_panel", 0) <= p <= scene.get("last_panel", 0)
           for p in reveal_panels if p is not None):
        reasons.append("contains a reveal")
    if n_d >= 6:
        reasons.append(f"{n_d} distinct exchanges")

    # An allowance only makes sense for a scene the CAP is actually flattening.
    # A scene scoring under the cap already has all the words its own content
    # earned it, so extra room would be padding, not rescue.
    if base < BUDGET_CAP or not reasons:
        return base, base, []

    granted = min(DENSE_HARD_CAP, int(base * DENSE_MULTIPLIER))
    return granted, base, reasons


SCRIPT_SYSTEM = """\
You are a master comic-recap narrator writing the voiceover for a recap video. \
You retell the chapter as one smooth story. You are NOT captioning images.

STYLE CONTRACT — every rule is mandatory:
1. Third person, past tense, story-first: retell events as one flowing narrative.
2. Every sentence must carry an EVENT, REACTION, REALIZATION, INTENTION or \
CONSEQUENCE. A sentence that only says how something looks is cut, or its \
detail folded into an action.
3. DIALOGUE FIDELITY: every meaningful exchange in these panels gets its own \
reported-speech sentence. Collapsing a whole conversation into one summary line \
is a contract violation. Only trivial filler — grunts, one-word reactions, \
repeated shouts — may fold into a neighbouring sentence.
4. Convert all visible dialogue into reported narration. NEVER use quotation \
marks. Panel text "Who are you?" becomes: he demanded to know who the stranger \
was.
5. NO panel, framing, camera or art language, ever. Never "the panel shows", \
"the image", "the frame", "close-up", "speed lines", "we see" — and never the \
word "camera" in any form.
6. Continue from where the previous scene's narration ended. Never re-introduce \
or re-tell something already narrated.
7. Appearance, clothing and setting appear ONLY when plot-relevant or when they \
set atmosphere — one economical touch, never an inventory.
8. Infer motive, emotion and subtext when the art or dialogue clearly implies \
it, the way a narrator who knows the story would. NEVER invent names, numbers, \
backstory or events the panels do not support.
9. DENSITY IS EDITORIAL, NOT MECHANICAL. Narrate the story, not the panels. A \
run of panels showing one continuous action gets ONE sentence. A filler or \
transition panel earns ZERO. Only a true story peak earns two or three. Never \
average sentences per panel.
10. WORLD-BUILDING, WHERE THE CHAPTER BUILDS IT. A recap of a fantasy or \
progression story is not just who did what — it is the world those events \
happen in. When the panels establish any of the following, carry it in the \
narration: the setting and its atmosphere; factions, ranks, titles and who \
outranks whom; how the power system works and what it costs; the stakes and \
the rules the world runs on; recurring objects, systems or terms; and the \
social dynamics between the people on screen.
11. HOW to carry it: woven into the action in the same sentence, never as an \
exposition dump and never as a lore paragraph. "He drew the blade his brother \
had been denied" carries a rank, a rivalry and a stake inside one action. \
Carry world detail ONLY where the chapter supports it — never invent a rank, a \
system, a place name or a rule the panels do not show, and never import \
knowledge from elsewhere in the series.

VOICE — match this cadence: sentences that move, reported speech, no scenery \
padding. Short declaratives for impact; a longer sentence to carry a turn.

OUTPUT: only the narration prose for this scene. No preamble, no labels, no \
panel references."""


def build_scene_prompt(scene, panels, cmap, budget, running_summary, tail):
    lines = []
    for p in panels:
        bits = [f"PANEL {p['n']}: {(p.get('visual_description') or '')[:240]}"]
        if (p.get("ocr_text") or "").strip() and not p.get("is_credits"):
            bits.append(f"  TEXT: {(p.get('ocr_text') or '')[:200]}")
        if p.get("is_credits"):
            bits.append("  (credits/watermark — carries no story)")
        lines.append("\n".join(bits))
    ctx = ""
    if running_summary:
        ctx += ("\nTHE CHAPTER SO FAR (do not re-tell any of it):\n"
                + running_summary + "\n")
    if tail:
        ctx += ("\nTHE NARRATION SO FAR ENDS WITH:\n" + tail
                + "\nContinue from there.\n")
    return (f"SCENE {scene.get('scene')} — {scene.get('title','')} "
            f"[{scene.get('phase','')}]\n{scene.get('summary','')}\n"
            + ctx
            + "\nPANELS IN ORDER:\n" + "\n".join(lines)
            + f"\n\nLENGTH — THIS IS A TARGET RANGE, NOT JUST A CEILING.\n"
              f"Write between {int(budget * TARGET_MIN_FRAC)} and "
              f"{int(budget * TARGET_MAX_FRAC)} words for this scene. "
              f"{budget} is the hard maximum.\n"
              "Coming in far UNDER the range is a failure, not economy: it "
              "means the scene's events, exchanges and world detail were "
              "flattened into a summary. Use the range. Do not pad it with "
              "scenery or repetition to reach it — if the scene genuinely has "
              "little in it, say so by writing less, but a scene with several "
              "panels and real dialogue almost never does.\n\n"
              "Write the narration for this scene now, at story density (fewer "
              "sentences than panels, but every real beat present):")


def script_plus(descs, cmap, model=None, progress=None):
    """Scene-aware, budgeted narration with an auditable dense-scene allowance."""
    model = model or MODEL
    client = validator._client()
    tally = _Tally()
    by_n = {d["n"]: d for d in descs}
    units, allowances = [], []
    running, tail = "", ""

    scenes = sorted(cmap.get("scenes") or [], key=lambda s: s.get("first_panel", 0))
    for si, scene in enumerate(scenes, start=1):
        lo = scene.get("first_panel", 0)
        hi = scene.get("last_panel", 0)
        panels = [by_n[n] for n in sorted(by_n) if lo <= n <= hi]
        panels = [p for p in panels if not p.get("is_credits")]
        if not panels:
            continue
        budget, base, reasons = scene_budget(scene, panels, cmap)
        if budget != base:
            allowances.append({"scene": scene.get("scene"), "base": base,
                               "granted": budget, "reasons": reasons})
        if progress:
            progress(f"Claude writing scene {si}/{len(scenes)} "
                     f"({len(panels)} panels, {budget} words)")
        resp, meter, cost = validator._call_claude(
            client, model=model, max_tokens=8000,
            system=[{"type": "text", "text": SCRIPT_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT},
            messages=[{"role": "user", "content": build_scene_prompt(
                scene, panels, cmap, budget, running, tail)}])
        tally.add(meter, cost)
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not text:
            continue
        words = len(text.split())
        n_d = dialogue_lines(panels)
        rich = len(panels) >= RICH_MIN_PANELS or n_d >= RICH_MIN_DIALOGUE
        spend = words / float(budget) if budget else 1.0
        units.append({"scene_id": len(units), "text": text,
                      "panel_ids": [p["panel_id"] for p in panels],
                      "panel_numbers": [p["n"] for p in panels],
                      "scene": scene.get("scene"), "phase": scene.get("phase"),
                      "budget": budget, "base_budget": base,
                      "words": words, "spend_frac": round(spend, 3),
                      "dialogue_lines": n_d,
                      # A content-rich scene written far under its range has
                      # been flattened, not economised. It is a failure state
                      # and is sent back for regeneration.
                      "under_spent": bool(rich and spend < TARGET_MIN_FRAC)})
        tail = text[-400:]
        running = (running + " " + (scene.get("summary") or ""))[-1200:]

    under = [{"unit": u["scene_id"], "scene": u["scene"],
              "panels": len(u["panel_numbers"]), "dialogue": u["dialogue_lines"],
              "budget": u["budget"], "words": u["words"],
              "spend_pct": round(u["spend_frac"] * 100, 1)}
             for u in units if u["under_spent"]]
    return units, tally.stats(
        scenes=len(units), allowances=allowances,
        target_range=[TARGET_MIN_FRAC, TARGET_MAX_FRAC],
        mean_spend_pct=round(100 * sum(u["spend_frac"] for u in units)
                             / max(1, len(units)), 1),
        under_spent=under,
        over_budget=sum(1 for u in units if u["words"] > u["budget"]))


# =============================================== STAGE: CRITIQUE / REVISE
CRITIQUE_SYSTEM = """\
You are a fact-checking script editor for a comic-recap narration. For each \
UNIT below, compare the DRAFT against the PANEL FACTS it was written from.

Report ONLY real problems. Types:
- hallucination: names, numbers, events or motives with NO support in the facts
- misorder: events narrated in a different order than the panels
- missed_beat: a clearly major story event in the facts the draft skips entirely
- style_violation: quoted dialogue, present tense, or ANY mention of the \
camera / panel / image / frame
- redundancy: re-tells events an EARLIER unit already narrated
- over_compression: several distinct exchanges or a reveal AND its reaction \
flattened into a single summary line, losing story the panels clearly carry
- weak_dialogue_coverage: meaningful dialogue in the panels that the draft does \
not report at all
- missing_worldbuilding: the panels establish setting, a faction or rank, a \
power-system rule, a stake, or a named object/system that matters, and the \
draft drops it entirely. Report this ONLY when the panel facts actually carry \
the detail — never because the narration "could say more".

An empty list is a normal and frequent result. Do not invent problems to look \
thorough, and do not report style preferences."""

CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit": {"type": "integer"},
                    "type": {"type": "string", "enum": [
                        "hallucination", "misorder", "missed_beat",
                        "style_violation", "redundancy", "over_compression",
                        "weak_dialogue_coverage", "missing_worldbuilding"]},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["unit", "type", "problem", "fix"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["issues"],
    "additionalProperties": False,
}


def underspend_issues(units):
    """Under-spend is measured, not judged — so it is raised in code rather
    than asked of the reviewer, which would just be a second opinion on
    arithmetic the pipeline already has."""
    out = []
    for u in units:
        if not u.get("under_spent"):
            continue
        out.append({
            "unit": u["scene_id"], "type": "over_compression",
            "problem": (f"this scene used {u['words']} words of a "
                        f"{u['budget']}-word budget "
                        f"({u['spend_frac']:.0%}) across "
                        f"{len(u['panel_numbers'])} panels and "
                        f"{u['dialogue_lines']} exchanges — its events have "
                        "been flattened into a summary"),
            "fix": (f"rewrite at "
                    f"{int(u['budget'] * TARGET_MIN_FRAC)}-"
                    f"{int(u['budget'] * TARGET_MAX_FRAC)} words, giving each "
                    "real exchange its own reported-speech sentence and "
                    "carrying the world detail the panels establish"),
        })
    return out


def critique(units, descs, model=None, progress=None):
    """One reviewer call over the whole draft. Returns typed issues."""
    model = model or MODEL
    client = validator._client()
    tally = _Tally()
    by_n = {d["n"]: d for d in descs}
    if progress:
        progress(f"Claude reviewing the draft ({len(units)} units)")

    blocks = []
    for u in units:
        facts = " | ".join(
            (by_n[n].get("visual_description") or "")[:110]
            + (f" [text: {(by_n[n].get('ocr_text') or '')[:60]}]"
               if (by_n[n].get("ocr_text") or "").strip() else "")
            for n in u.get("panel_numbers", []) if n in by_n)
        blocks.append(f"UNIT {u['scene_id']}\nPANEL FACTS: {facts}\n"
                      f"DRAFT: {u['text']}")

    resp, meter, cost = validator._call_claude(
        client, model=model, max_tokens=16000,
        system=[{"type": "text", "text": CRITIQUE_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT,
                       "format": {"type": "json_schema",
                                  "schema": CRITIQUE_SCHEMA}},
        messages=[{"role": "user", "content": "\n\n".join(blocks)}])
    tally.add(meter, cost)
    issues = [i for i in validator._parse_json_reply(resp).get("issues", [])
              if isinstance(i.get("unit"), int)]
    return issues, tally.stats(issues=len(issues))


def revise(units, issues, descs, cmap, model=None, progress=None):
    """Regenerate ONLY the flagged units, with the editor's notes attached.

    Provenance is preserved by construction: a revised unit keeps the same
    scene and the same panels, so nothing downstream has to re-derive it.
    """
    model = model or MODEL
    if not issues:
        return units, {"calls": 0, "cost_usd": 0.0, "prompt_tokens": 0,
                       "output_tokens": 0, "revised": 0}
    client = validator._client()
    tally = _Tally()
    by_n = {d["n"]: d for d in descs}
    by_unit = {}
    for i in issues:
        by_unit.setdefault(i["unit"], []).append(i)

    scenes = {s.get("scene"): s for s in (cmap.get("scenes") or [])}
    revised = 0
    out = list(units)
    for uid in sorted(by_unit)[:MAX_REVISIONS]:
        u = next((x for x in out if x["scene_id"] == uid), None)
        if u is None:
            continue
        notes = "\n".join(f"- {i['type']}: {i['problem']} FIX: {i['fix']}"
                          for i in by_unit[uid])
        panels = [by_n[n] for n in u.get("panel_numbers", []) if n in by_n]
        if not panels:
            continue
        if progress:
            progress(f"Claude revising unit {uid} "
                     f"({len(by_unit[uid])} issue(s))")
        base = build_scene_prompt(scenes.get(u.get("scene"), {}), panels, cmap,
                                  u["budget"], "", "")
        resp, meter, cost = validator._call_claude(
            client, model=model, max_tokens=8000,
            system=[{"type": "text", "text": SCRIPT_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT},
            messages=[{"role": "user", "content":
                       base + "\n\nEDITOR NOTES on the previous draft (you MUST "
                              "fix these):\n" + notes
                       + "\n\nPREVIOUS DRAFT:\n" + u["text"]
                       + "\n\nRewrite the narration for this scene now:"}])
        tally.add(meter, cost)
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if text:
            u["text"] = text
            u["words"] = len(text.split())
            u["revised_for"] = [i["type"] for i in by_unit[uid]]
            revised += 1
    return out, tally.stats(revised=revised)


# ======================================================== STAGE: CROP
CROP_SYSTEM = """\
You are a webtoon recap video editor choosing how each panel is framed on \
screen. The narration is already written and already assigned, so you know \
exactly what each panel is being used to say.

For each item you are given the panel image, the narration line that plays over \
it, and the lines immediately before and after.

Decide what the viewer must SEE for that line to land, then return the crop \
that fills the video frame with it.

- Default to the FULL PANEL. Cropping is for when the line is about something \
specific within the panel; a panel whose whole composition carries the line \
should be left alone. Returning [0, 0, 1, 1] is a good, common answer.
- Crop in when the line is about a face, an expression, a reaction, a gesture, \
a hand, an object, a weapon, a wound, or a piece of text that the viewer must \
read. Judge this from what the LINE is about — not from whether a particular \
word appears in it. A line about dawning horror deserves the face even though \
it names no body part.
- Never crop away text the line depends on.
- Never return a sliver. If the subject cannot be isolated cleanly, return the \
full panel.
- Tall panels often need a vertical window rather than the whole strip; a wide \
establishing shot usually wants the whole frame.

Return for each item:
- `crop`: [x0, y0, x1, y1] as fractions, or [0, 0, 1, 1] for the full panel
- `framing`: "close_up", "medium", or "full"
- `reason`: one short clause on what the viewer needs to see and why
- `confidence`: 0.0-1.0 in this framing choice"""

CROP_SCHEMA = {
    "type": "object",
    "properties": {
        "crops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "crop": {"type": "array", "items": {"type": "number"}},
                    "framing": {"type": "string",
                                "enum": ["close_up", "medium", "full"]},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["n", "crop", "framing", "reason", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["crops"],
    "additionalProperties": False,
}

# Production's floor, ported: a crop smaller than this is a mislocated subject,
# not a close-up.
CROP_MIN_AREA = float(os.environ.get("PLUS_CROP_MIN_AREA", 0.12))
CROP_CONF_FLOOR = float(os.environ.get("PLUS_CROP_CONF_FLOOR", 0.45))
CROPS_PER_CALL = int(os.environ.get("PLUS_CROPS_PER_CALL", 4))


def _crop_area(box):
    try:
        x0, y0, x1, y1 = box
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)
    except (TypeError, ValueError):
        return 0.0


def plan_crops(pdir, placements, model=None, progress=None):
    """THE ORDERING FIX — crops chosen after the script and the placement.

    `placements` is [{panel, line, prev, next}], one per panel that actually
    carries narration. A panel nothing plays over never gets a crop call: there
    is no line for the framing to serve, so the full panel is correct and free.

    Every returned box is validated against the same floor production uses, and
    anything too small, malformed or low-confidence falls back to the full
    panel — a sliver is always worse than no crop.
    """
    model = model or MODEL
    if not placements:
        return {}, {"calls": 0, "cost_usd": 0.0, "prompt_tokens": 0,
                    "output_tokens": 0, "planned": 0}
    client = validator._client()
    tally = _Tally()
    out, rejected = {}, []

    batches = [placements[i:i + CROPS_PER_CALL]
               for i in range(0, len(placements), CROPS_PER_CALL)]
    for bi, batch in enumerate(batches, start=1):
        if progress:
            progress(f"Claude framing panels ({bi}/{len(batches)})")
        content = []
        for item in batch:
            p = item["panel"]
            img = _image_block(pdir, p)
            if img is None:
                continue
            content.append({"type": "text", "text":
                            f"ITEM {p['n']} — the line that plays over this "
                            f"panel:\n\"{item['line']}\"\n"
                            + (f"(previous line: {item.get('prev','')})\n"
                               if item.get("prev") else "")
                            + (f"(next line: {item.get('next','')})\n"
                               if item.get("next") else "")})
            content.append(img)
        if not content:
            continue
        content.append({"type": "text",
                        "text": "Frame each item above, by its number."})
        resp, meter, cost = validator._call_claude(
            client, model=model, max_tokens=8000,
            system=[{"type": "text", "text": CROP_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT,
                           "format": {"type": "json_schema",
                                      "schema": CROP_SCHEMA}},
            messages=[{"role": "user", "content": content}])
        tally.add(meter, cost)

        known = {item["panel"]["n"]: item["panel"] for item in batch}
        # The line each panel carries, so the gate can judge the crop against
        # what the beat is actually about.
        item_line = {item["panel"]["n"]: item.get("line", "") for item in batch}
        for c in validator._parse_json_reply(resp).get("crops", []):
            p = known.get(c.get("n"))
            if p is None:
                continue
            # The RAW box is measured first. _clean_crop() normalises a
            # malformed or vanishing box straight to full-frame, so checking
            # the area afterwards can only ever see 1.0 — the rejection would
            # be applied but never attributable, which is the silent failure
            # this whole module exists to avoid.
            # The RAW box goes to the gate. Running _clean_crop first would
            # normalise a sliver to full-frame before the gate ever saw it, so
            # the refusal would be applied but never attributable — the same
            # silent-downgrade bug this module already fixed once.
            raw = c.get("crop")
            conf = _clamp01(c.get("confidence"), 0.5)
            # THE COMPOSITION GATE, shared with production. Measures the box
            # against the panel's own pixels and against the full-frame
            # baseline; a crop is kept only if it wins by a real margin and
            # passes linting.
            #
            # `confidence` is recorded but is NOT the gate and must never
            # become one: the two worst boxes in the Martial Genius audit both
            # carried 1.0 (shot_planner.py:64).
            decision = {"used": "crop", "why": "", "crop_score": None,
                        "full_score": None}
            box = _clean_crop(raw)
            path = os.path.join(pdir, "crops",
                                p.get("file") or f"{p['panel_id']}.png")
            try:
                import crop_score
                box, decision = crop_score.choose_crop(
                    raw, path, item_line.get(p["n"], ""))
            except ImportError:
                box = _clean_crop(raw)
            if decision.get("used") == "full" and decision.get("downgraded"):
                rejected.append({"panel_id": p["panel_id"],
                                 "why": decision.get("why", ""),
                                 "crop_score": decision.get("crop_score"),
                                 "full_score": decision.get("full_score"),
                                 "confidence": conf})
            out[p["panel_id"]] = {
                "crop_bbox_norm": box,
                "framing_mode": c.get("framing", "full"),
                "focus_reason": (decision.get("why") or c.get("reason", "")),
                "focus_confidence": conf,
                "crop_score": decision.get("crop_score"),
                "full_frame_score": decision.get("full_score"),
                "crop_downgraded": bool(decision.get("downgraded")),
            }

    full = sum(1 for v in out.values()
               if _crop_area(v["crop_bbox_norm"]) > 0.98)
    return out, tally.stats(planned=len(out), batches=len(batches),
                            full_frame=full, cropped=len(out) - full,
                            rejected=rejected)
