"""Direct speech — 2-3 pivotal lines per CHAPTER, performed by the one narrator.

docs/craft_reconciliation.md P2, with the owner's decisions (§12):
  * the whole chapter gets two or three direct lines, no more — the reference
    recap used 2 in 41,655 words — reserved for the lines the chapter turns on;
  * a line is quotable only if the reader tied it to a speaker by the bubble's
    tail or position (ocr_lines.is_attributable) AND that speaker can be named
    from the chapter summary (the reader's labels drift between panels —
    "wild-haired man" / "curly-haired man" — so a label alone is not an
    identity).

Scenes are written one at a time, so a chapter-wide cap cannot be left to each
scene's writer. Instead:
  1. candidates()   — code lists every attributable line, numbered;
  2. select()       — ONE model call picks up to 3 by NUMBER (it cannot invent
                      or reword a line) and names each speaker;
  3. validate()     — code keeps only picks whose number exists and whose
                      speaker name appears in the chapter summary, max 3;
  4. scene_block()  — each scene's prompt is told exactly which lines it may
                      quote, or that it may quote none;
  5. audit()        — after drafting, code flags any quote that is not an
                      approved line of that scene, and a chapter over the cap.
Both engines use this module, so they cannot drift apart.
"""
import json
import os
import re

import ocr_lines


def enabled(override=None):
    """Is direct speech ON for this run? An ingest's explicit choice wins;
    otherwise the DIRECT_SPEECH env var (Railway), OFF unless set to 1/true/on.
    Off means no lines are selected, every scene is told to quote nothing, and
    the chapter is narrated in reported speech exactly as before P2 — so
    merging P2 changes no chapter until the owner turns it on (rule 36)."""
    if override is not None:
        return bool(override)
    return os.environ.get("DIRECT_SPEECH", "").strip().lower() in ("1", "true", "on", "yes")

# The rule both engines' style contracts include VERBATIM (narrate.py rule 3,
# claude_plus.SCRIPT_SYSTEM rule 4). One copy, so the engines cannot drift.
RULE_TEXT = """DIALOGUE IS PERFORMED BY THE ONE NARRATOR — NEVER BY A SECOND VOICE. \
The whole chapter quotes only two or three lines of dialogue directly — the \
lines it turns on — and they have already been chosen: they appear under \
DIRECT SPEECH FOR THIS SCENE when this scene holds one. Quote exactly those \
lines and no others. Put the character's words in double quotation marks with \
a brief attribution tag that names the speaker and at most one physical action \
(he said / she snapped / the old man muttered, already reaching for the \
crossbow). You are a single storyteller saying that line aloud in YOUR OWN \
voice: the line is carried ONLY by the attribution tag, a pacing beat around it \
(a short sentence before it, or the line standing alone as its own sentence), \
and the words themselves. Never write it as if a different voice, an \
impression, an accent or a character performance delivers it — no "in a deep \
growl", no "in a squeaky voice", no "mimicking", no bracketed or \
parenthesised stage directions, no dialect spelling, no phonetic accents, no \
stretched letters. The listener must always hear the narrator reporting what \
was said, in the narrator's voice. The quotation marks are for the script only \
— the pipeline removes them before the narration is voiced — so never write \
the words "quote", "unquote" or "in quotes", and never announce that someone \
is being quoted. Every other line of dialogue is retold as reported narration \
(panel text "Who are you?" becomes: he demanded to know who the stranger was); \
trivial one-word reactions and grunts fold into reported narration or are \
dropped. Narration boxes are never quoted — they become your own narration, \
unannounced."""

MAX_PER_CHAPTER = 3
MIN_WORDS = 2            # a lone "Run!" can be pivotal, but "…" / "?!" cannot

_QUOTE_RE = re.compile(r'["“]([^"”]{1,400})["”]')
_WORD_RE = re.compile(r"[a-z0-9']+")


def _words(s):
    return _WORD_RE.findall((s or "").lower())


def candidates(panels):
    """Every line the reader attributed by tail or position, in chapter order."""
    out = []
    for p in panels:
        for l in p.get("lines") or []:
            if ocr_lines.is_attributable(l) and len(_words(l["text"])) >= MIN_WORDS:
                out.append({"n": len(out) + 1, "panel_id": p.get("panel_id"),
                            "text": l["text"], "label": l["speaker"],
                            "type": l["type"]})
    return out


def selection_prompt(cands, chapter_summary):
    rows = "\n".join(f'{c["n"]}. [{c["type"]}] {c["label"]}: "{c["text"]}"'
                     for c in cands)
    return f"""You are choosing the ONLY lines of dialogue a recap narrator will quote directly in this whole chapter. Everything else will be retold in reported speech.

Pick TWO or THREE lines — fewer only if the chapter truly has nothing pivotal. A line qualifies only if the chapter TURNS on it: a threat that changes the fight, a reveal, a decision, a refusal, the line a scene is remembered by. Never pick filler, exposition, or a line whose meaning needs the lines around it.

For each pick, name the speaker using a character name from the CHAPTER SUMMARY. The label in the list is only how that panel looked ("the wild-haired man"); map it to the character the summary describes. If you cannot tie a line to a named character from the summary with confidence, do not pick it.

CHAPTER SUMMARY:
{chapter_summary}

CANDIDATE LINES (number. [type] panel label: "words"):
{rows}

Answer with JSON only: {{"picks": [{{"n": <number>, "speaker": "<name from the summary>", "why": "<one short clause>"}}]}}"""


# A speaker part that is only one of these words identifies nobody — it would
# "match" any summary — so it never satisfies the guard on its own.
_GENERIC = {"man", "woman", "boy", "girl", "person", "figure", "guy", "he",
            "she", "they", "someone", "character", "people", "crowd"}


def _speaker_in_summary(name, summary):
    """The label guard. Models write compound speakers — "The Masked Ninja
    (The Spy)", "Hector / the old hunter" — and a chapter may never give its
    cast real names (Murim ch.44's own summary says "the protagonist", "a
    spy"). So: accept the speaker when ANY part of it is a character the
    summary refers to, ignoring a leading article and bare generic nouns.
    Returns the matched part (used as the attribution name) or None."""
    parts = [name] + re.split(r"\s*[()/]\s*|\s+or\s+|,\s*", name)
    for part in parts:
        core = re.sub(r"^(the|a|an)\s+", "", part.strip(), flags=re.I).strip()
        if len(core) < 3 or core.lower() in _GENERIC:
            continue
        if re.search(r"\b" + re.escape(core.lower()) + r"\b", summary):
            return part.strip()
    return None


def validate(raw_picks, cands, chapter_summary):
    """Keep only picks that exist, are named from the summary, max 3, in
    chapter order, one per candidate."""
    by_n = {c["n"]: c for c in cands}
    summary = (chapter_summary or "").lower()
    kept, seen = [], set()
    for p in raw_picks if isinstance(raw_picks, list) else []:
        if not isinstance(p, dict):
            continue
        try:
            n = int(p.get("n"))
        except (TypeError, ValueError):
            continue
        c = by_n.get(n)
        name = str(p.get("speaker") or "").strip()
        if c is None or n in seen or len(name) < 2:
            continue
        # the label guard: the speaker must be a character the summary names
        matched = _speaker_in_summary(name, summary)
        if not matched:
            continue
        seen.add(n)
        kept.append(dict(c, speaker=matched, why=str(p.get("why") or "")[:160]))
    kept.sort(key=lambda c: c["n"])
    return kept[:MAX_PER_CHAPTER]


def parse_picks(raw_text):
    m = re.search(r"\{.*\}", raw_text or "", re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0)).get("picks") or []
    except (json.JSONDecodeError, AttributeError):
        return []


def select(panels, chapter_summary, ask):
    """ask(prompt) -> model text. Returns the approved lines (may be empty).
    A chapter with no attributable candidates costs no model call."""
    cands = candidates(panels)
    if not cands:
        return []
    return validate(parse_picks(ask(selection_prompt(cands, chapter_summary))),
                    cands, chapter_summary)


def for_panels(approved, panel_ids):
    ids = set(panel_ids)
    return [a for a in approved if a["panel_id"] in ids]


def scene_block(lines_for_scene):
    """The part of a scene prompt that says what may be quoted."""
    if not lines_for_scene:
        return ("DIRECT SPEECH FOR THIS SCENE: none. Retell every line of "
                "dialogue in this scene as reported narration — no quotation "
                "marks at all.")
    rows = "\n".join(f'- {a["speaker"]}: "{a["text"]}"' for a in lines_for_scene)
    return ("DIRECT SPEECH FOR THIS SCENE — the ONLY line(s) you may quote, "
            "chosen for the whole chapter. Quote each once, with its speaker's "
            "name in the attribution tag; you may fix ALL-CAPS and trim to the "
            "core clause, never add words. Every other line stays reported "
            "narration:\n" + rows)


def _matches(quote, approved_text):
    q, a = _words(quote), _words(approved_text)
    if not q or not a:
        return False
    # a trimmed quote must be a run of the approved line's words
    qs, as_ = " ".join(q), " ".join(a)
    return qs in as_


def quotes_in(text):
    return [m.group(1) for m in _QUOTE_RE.finditer(text or "")]


def audit(units, approved):
    """units: [{"unit": int, "text": str, "panel_ids": [...]}]. Returns
    (issues, summary). Issues use the critique's shape so the reviser can
    act on them: unapproved_quote per stray quote; chapter_quote_cap when the
    chapter as a whole carries more than MAX_PER_CHAPTER quotes."""
    issues, total, stray = [], 0, 0
    for u in units:
        allowed = for_panels(approved, u.get("panel_ids") or [])
        for q in quotes_in(u.get("text")):
            total += 1
            if not any(_matches(q, a["text"]) for a in allowed):
                stray += 1
                issues.append({
                    "unit": u["unit"], "type": "unapproved_quote",
                    "problem": f'"{q[:80]}" is quoted, but it is not one of the '
                               "chapter's approved direct-speech lines for this scene",
                    "fix": "retell it as reported narration, without quotation marks"})
    used = sum(1 for a in approved
               if any(_matches(q, a["text"]) for u in units
                      for q in quotes_in(u.get("text"))))
    # the cap counts LINES, not quote spans: an approved line split around its
    # tag ("One more step," he said, "and you leave…") is one line, two spans
    lines_quoted = used + stray
    if lines_quoted > MAX_PER_CHAPTER and issues:
        issues.append({"unit": issues[0]["unit"], "type": "chapter_quote_cap",
                       "problem": f"the chapter quotes {lines_quoted} lines; the "
                                  f"cap is {MAX_PER_CHAPTER} for the whole chapter",
                       "fix": "keep only the approved lines as direct speech"})
    return issues, {"approved": len(approved), "approved_used": used,
                    "quote_spans": total, "quotes_unapproved": stray,
                    "lines_quoted": lines_quoted}
