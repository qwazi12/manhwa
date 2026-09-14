"""Board validator — the chapter-level review chain that runs BEFORE the owner.

The storyboard is assembled by machines that are not, and will never be, 100%
accurate: a splitter cuts panels, an OCR pass reads text off them, a vision
model describes them, a matcher decides which narration line lands on which
panel, and a segmenter decides how long each one is on screen.

THE FIRST VERSION OF THIS FILE WAS TOO LOCAL. It asked, of each row in
isolation, "does this line plausibly fit this panel?" — which cannot see the
failures that actually hurt a recap, because those are failures of SEQUENCE: a
reveal shown before its setup, a reaction before its cause, a line that fits
the panel it landed on but belonged four rows earlier. A row-local checker
reads every one of those as fine.

So the chain now builds a model of the WHOLE CHAPTER first, and judges every
row against its place in that chapter:

  rules -> A: chapter map -> B: sequence -> D: description/OCR -> E: vision
  free     one call          overlapping    batched              flagged +
                             windows                             spot checks

  A  Chapter map. One call that reads the narration in order and returns scene
     boundaries, the arc phase of each scene, where the reveals land, the
     chronology and the emotional turns. Everything downstream is judged
     against this, and it travels as a cacheable prefix so it is paid for once.

  B  Sequence validation. Windows of rows judged IN ORDER against the map:
     narration landing early or late, reveal-before-setup, reaction-before-
     cause, a line merely topically related to the panel it sits on, and pacing
     that hurts the flow. Windows OVERLAP, because a mismatch whose evidence
     straddles a batch edge is invisible to non-overlapping batches.

  D  Description / OCR sanity. Explicitly hunts the case where the DESCRIPTION
     is the thing that is wrong and has poisoned the match downstream — the
     failure every other pass is built on top of and therefore cannot see.

  E  Vision. Opens the real crop for flagged rows AND for a stratified sample
     of rows nothing flagged. The sample is the point: passes B and D reason
     from descriptions, so a description that is wrong *and plausible* produces
     a wrong row that looks clean to them. Spot checks are the only way the
     chain can see into that blind spot at all.

Findings are not a report. Each one carries ACTIONS that operate on the real
board — swap the match, move the line, leave the panel out, re-describe it —
through the same storyboard_edit functions the board itself uses, so they are
undoable and can never drift from board state. See validator_actions.py.

Accepted and dismissed findings are remembered per project and fed back into
the next run, so a judgement the owner has already made is not re-litigated
every chapter.

Cost: every Claude call goes through usage.gate('claude', 1, model=...), so it
is capped by the same per-job and daily-spend guardrails as Gemini and TTS,
metered from the tokens the API actually reports, and shows up in the same cost
header on the site. There is no path in this file that reaches the API without
passing that gate.

The rule pass needs no credentials and always runs. The Claude passes raise a
clear error naming the key they need (CLAUDE_API_KEY, or ANTHROPIC_API_KEY as
a fallback) when it is missing, rather than degrading into a validation that
silently checked less than it claimed to.
"""

import base64
import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timezone

import usage

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_NAME = "validation.json"
FEEDBACK_NAME = "validation_feedback.json"

# ---------------------------------------------------------------- config
# Opus is the default because this job IS the accuracy backstop — the whole
# point is catching what a cheaper model already missed. Override to
# claude-sonnet-5 or claude-haiku-4-5 to trade accuracy for cost; the rate card
# in usage.py prices all three.
MODEL = os.environ.get("VALIDATOR_MODEL", "claude-opus-5")

# Effort controls how hard Claude thinks per call. 'medium' is the default
# because the rule pass has already removed the mechanical findings and the
# chapter map has already framed the question. Raise for a pre-publish sweep.
EFFORT = os.environ.get("VALIDATOR_EFFORT", "medium")

# Rows per sequence window, and how many rows consecutive windows SHARE.
# The overlap is not redundancy: an out-of-place line is recognised by seeing
# the row it belongs to, so any mismatch whose two halves fall on opposite
# sides of a window edge is invisible without it. Findings carry a stable id,
# so a row seen twice cannot be reported twice.
BATCH_ROWS = int(os.environ.get("VALIDATOR_BATCH_ROWS", 25))
SEQ_OVERLAP = int(os.environ.get("VALIDATOR_SEQ_OVERLAP", 5))

# Hard ceiling on the vision pass; image calls are the expensive ones.
MAX_VISION_ROWS = int(os.environ.get("VALIDATOR_MAX_VISION_ROWS", 24))
# …of which this many are spent on rows NOTHING flagged, spread evenly across
# the chapter. This is the only check on the chain's own blind spot, so it is
# deliberately not zero.
SPOT_CHECK_ROWS = int(os.environ.get("VALIDATOR_SPOT_CHECK_ROWS", 6))

# Panel crops run to 760x1598 and larger. Anything past ~1200px on the long
# edge costs tokens without telling Claude anything new about a comic panel.
VISION_MAX_PX = int(os.environ.get("VALIDATOR_VISION_MAX_PX", 1200))

# Timing thresholds for the rule pass.
#
# NOTE ON A CHECK THAT IS DELIBERATELY ABSENT: "can the narration be said in
# the time available" is NOT checkable here, because it cannot fail. The
# timeline is DERIVED from the TTS audio — beats carry the real aligned windows
# and segments are fitted to them — so words always fit by construction. An
# earlier version checked words-per-second and raised 27 false alarms on a
# clean chapter, because folded panels all carry a copy of their shared beat's
# full text: six panels sharing one 24-word beat each looked like they had to
# speak 24 words in their own slice. The checks below can actually fail.
#
# A panel under FLASH_SEC is on screen too briefly to register as an image.
FLASH_SEC = float(os.environ.get("VALIDATOR_FLASH_SEC", 1.0))
# A panel held this long with no narration over it is dead air.
LONG_SILENT_HOLD_SEC = float(os.environ.get("VALIDATOR_LONG_HOLD_SEC", 12.0))
# A run of consecutive segments on ONE panel, this long or longer, is a stall.
SAME_PANEL_RUN_SEC = float(os.environ.get("VALIDATOR_SAME_PANEL_RUN_SEC", 15.0))

# Severity is CONFIDENCE THAT THE ROW IS WRONG, not importance. The labels are
# what the drawer shows, so the three levels read as a judgement rather than as
# a colour.
SEVERITIES = ("high", "medium", "low")
SEVERITY_LABELS = {
    "high": "Definitely wrong",
    "medium": "Likely wrong",
    "low": "Worth review",
}

# Category is WHAT KIND of defect, independent of how sure we are. The pairing
# is what lets the drawer group "three order problems" apart from "two poisoned
# descriptions" instead of showing one flat list ranked only by colour.
CATEGORIES = {
    "placement": "Wrong placement",
    "order": "Order problem",
    "continuity": "Scene continuity",
    "pacing": "Pacing / hold",
    "description": "Likely description error",
    "ocr": "Likely OCR poison",
    "coverage": "Coverage",
}

# Scanlation groups stamp credit pages with their own name, a chapter number
# and a Discord/site URL. Those pages are never story content, and a narration
# line landing on one is always wrong.
_CREDIT_MARKERS = re.compile(
    r"(scans?\b|scanlation|discord\.gg|\.com\b|\.net\b|telegram|patreon|"
    r"translator|proofread|redraw|typeset|\bTL\b|\bPR\b|\bQC\b)",
    re.IGNORECASE)

# A description that could be written about almost any panel cannot have driven
# a real match. Cheap to spot with a rule; no need to pay a model for it.
_GENERIC_DESC = re.compile(
    r"^(a |an |the )?(character|person|man|woman|figure|panel|scene|image)\b",
    re.IGNORECASE)


class ValidatorError(RuntimeError):
    """Raised for conditions the operator must see and act on — a missing key,
    an unusable model reply — as opposed to a finding about the board."""


# ------------------------------------------------------------------ rows
def _natural(pid):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", pid or "")]


def _words(text):
    return len((text or "").split())


def build_rows(pdir, review=None):
    """One dict per board row, carrying exactly the five columns the owner
    reads — Panel, System OCR, System description, Script placement, On-screen
    timing & motion — plus the handles an ACTION needs (seg_index, timeline
    position) so a finding can be acted on rather than only reported.

    Deliberately built from the same JSON the storyboard renders from, not by
    scraping the rendered HTML: the validator has to judge the DATA, so that a
    finding points at something fixable rather than at a formatting artefact.
    """
    review = review or {}

    def _load(name, default):
        try:
            with open(os.path.join(pdir, name), encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    descs = _load("descriptions.json", [])
    descs.sort(key=lambda r: _natural(r.get("panel_id", "")))
    scenes = _load("script.json", [])
    segs = _load("segments.json", [])

    # Which narration unit claims each panel, and whether this panel is the one
    # that CARRIES the line or merely shares the unit's window with others.
    unit_of, unit_rank = {}, {}
    for sc in scenes:
        pids = sc.get("panel_ids", []) or []
        for i, pid in enumerate(pids):
            unit_of[pid] = sc
            unit_rank[pid] = i

    seg_of = {}
    for s in segs:
        seg_of.setdefault(s.get("panel_id"), []).append(s)

    # Timeline position, which reorder() addresses by. Computed once here so
    # every action that moves a line speaks the same coordinates as the board.
    ordered = sorted(segs, key=lambda s: s.get("start", 0))
    pos_of = {s.get("seg_index"): i for i, s in enumerate(ordered)}

    rows = []
    for i, d in enumerate(descs, start=1):
        pid = d.get("panel_id", "")
        sc = unit_of.get(pid)
        mine = sorted(seg_of.get(pid, []), key=lambda x: x.get("start", 0))
        seg = mine[0] if mine else None

        in_video = False
        if seg is not None:
            in_video = bool(seg.get("user_included")) and \
                review.get(str(seg.get("seg_index")), {}).get("status") != "rejected"

        if sc is None:
            placement = {"role": "left_out", "unit": None, "text": ""}
        else:
            placement = {
                "role": "carries" if unit_rank.get(pid, 0) == 0 else "shared",
                "unit": sc.get("scene_id"),
                "text": sc.get("text", "") or "",
                "panel_slot": unit_rank.get(pid, 0) + 1,
                "panel_count": len(sc.get("panel_ids", []) or []),
            }

        spoken = " ".join(b.get("text", "") for b in (seg.get("beats") or [])) \
            if seg else ""
        si = seg.get("seg_index") if seg else None
        rows.append({
            "n": i,
            "panel_id": pid,
            "file": d.get("file") or f"{pid}.png",
            "width": d.get("width"), "height": d.get("height"),
            "ocr": (d.get("ocr_text") or "").strip(),
            "desc": (d.get("visual_description") or "").strip(),
            "desc_ok": bool(d.get("ok", True)),
            "placement": placement,
            "timing": {
                "seg_index": si,
                "pos": pos_of.get(si),
                "seg_count": len(mine),
                "dur": round(sum(float(x.get("dur", 0.0)) for x in mine), 2)
                       if mine else None,
                "start": round(float(seg.get("start", 0.0)), 2) if seg else None,
                "in_video": in_video,
                "spoken_words": _words(spoken),
                "silent": bool(seg and not (seg.get("beats") or [])),
            },
        })
    return rows


def chapter_units(rows):
    """The narration in story order, with the panels each unit owns. This is
    the chapter as a NARRATIVE rather than as a table, and it is what the
    chapter-map pass reads."""
    by_unit = {}
    for r in rows:
        u = r["placement"]["unit"]
        if u is None:
            continue
        slot = by_unit.setdefault(u, {"unit": u, "text": r["placement"]["text"],
                                      "rows": [], "in_video": False})
        slot["rows"].append(r["n"])
        if r["timing"]["in_video"]:
            slot["in_video"] = True
    return [by_unit[u] for u in sorted(by_unit)]


# ------------------------------------------------------------- findings
def finding_id(panel_id, category):
    """Stable across runs, so an accepted or dismissed judgement survives a
    re-validation. Deliberately keyed on the PANEL and the KIND of problem, not
    on the wording — the wording is model-written and changes every run, which
    would make every finding look new and defeat the feedback loop."""
    return hashlib.sha1(
        f"{panel_id}|{category}".encode("utf-8")).hexdigest()[:12]


def _finding(row, column, severity, issue, suggestion, source,
             confidence=1.0, category="placement", target=None, scene=None):
    t = row.get("timing") or {}
    return {
        "id": finding_id(row.get("panel_id"), category),
        "row": row.get("n"),
        "panel_id": row.get("panel_id"),
        "seg_index": t.get("seg_index"),
        "pos": t.get("pos"),
        "column": column,
        "category": category,
        "category_label": CATEGORIES.get(category, category),
        "severity": severity,
        "severity_label": SEVERITY_LABELS.get(severity, severity),
        "issue": issue,
        "suggestion": suggestion,
        "source": source,
        "confidence": round(float(confidence), 2),
        "scene": scene,
        # The row this line probably belongs to, when a pass could name one.
        # It is what turns "wrong panel" into a one-click swap.
        "target_row": target,
    }


# --------------------------------------------------------- pass 1: rules
def rule_findings(rows):
    """Everything with an exact answer. Free, instant, and never wrong about
    arithmetic — which is precisely why it does not go to a model."""
    out = []
    last_unit = None

    for r in rows:
        t, p = r["timing"], r["placement"]

        # --- System description ------------------------------------------
        if not r["desc"]:
            out.append(_finding(
                r, "System description", "high",
                "No description was produced for this panel.",
                "Re-run describe for this panel — the matcher had nothing to "
                "match on, so any line placed here was placed blind.",
                "rules", category="description"))
        elif not r["desc_ok"]:
            out.append(_finding(
                r, "System description", "medium",
                "The vision pass marked this description as not ok.",
                "Re-run describe for this panel before trusting its placement.",
                "rules", category="description"))
        elif _GENERIC_DESC.match(r["desc"]) and len(r["desc"]) < 60 \
                and p["role"] != "left_out":
            out.append(_finding(
                r, "System description", "low",
                "The description is generic enough to fit almost any panel, so "
                "it cannot have driven a real match.",
                "Re-run describe for this panel, then re-check its placement.",
                "rules", confidence=0.6, category="description"))

        # --- System OCR ---------------------------------------------------
        # A credit page carrying narration is always a defect; a credit page
        # already left out is the pipeline working, and must not be reported.
        if r["ocr"] and len(_CREDIT_MARKERS.findall(r["ocr"])) >= 2:
            if p["role"] != "left_out":
                out.append(_finding(
                    r, "System OCR", "high",
                    "OCR reads as a scanlation credit/watermark page, but "
                    f"narration unit {p['unit']} is placed on it.",
                    "Leave this panel out — credit pages carry no story.",
                    "rules", category="ocr"))

        # --- On-screen timing & motion ------------------------------------
        if t["in_video"] and t["dur"]:
            if t["dur"] < FLASH_SEC:
                out.append(_finding(
                    r, "On-screen timing & motion", "medium",
                    f"On screen for only {t['dur']}s — too brief to read as a "
                    "panel; it will register as a flicker.",
                    "Drop this panel from the cut, or take time from a "
                    "neighbouring panel in the same narration unit.",
                    "rules", category="pacing"))
            elif t["silent"] and t["dur"] >= LONG_SILENT_HOLD_SEC:
                out.append(_finding(
                    r, "On-screen timing & motion", "medium",
                    f"{t['dur']}s on screen with no narration over it.",
                    "Shorten the hold or move a line onto it — this reads as "
                    "dead air.", "rules", category="pacing"))

        # --- Script placement --------------------------------------------
        # Units are authored in story order. A panel pulling an EARLIER unit
        # than the panel above it means the recap jumps backwards.
        if p["role"] == "carries" and p["unit"] is not None:
            if last_unit is not None and p["unit"] < last_unit:
                out.append(_finding(
                    r, "Script placement", "high",
                    f"Narration unit {p['unit']} appears after unit "
                    f"{last_unit} — the story runs backwards here.",
                    "Re-check the match for this panel and its neighbours.",
                    "rules", category="order"))
            last_unit = max(last_unit, p["unit"]) if last_unit is not None else p["unit"]

    # --- Several segments stacked on ONE panel -----------------------------
    # The stall an owner actually notices while watching: the narration moves
    # on through two or three beats while the picture never changes.
    #
    # This is checked PER ROW, not by walking for consecutive rows sharing a
    # panel — build_rows emits exactly one row per panel, so a "run" of rows
    # sharing a panel can never occur and a loop looking for one silently never
    # fires. The run lives in the row's segment COUNT and its summed duration.
    for r in rows:
        t = r["timing"]
        if not t["in_video"] or (t.get("seg_count") or 0) < 2:
            continue
        if (t["dur"] or 0) >= SAME_PANEL_RUN_SEC:
            out.append(_finding(
                r, "On-screen timing & motion", "medium",
                f"{t['seg_count']} segments in a row sit on this one panel for "
                f"{t['dur']}s together.",
                "Give some of that time to other panels, or cut the run short "
                "— the video stalls on one image here.",
                "rules", category="pacing"))

    # --- Coverage ---------------------------------------------------------
    # THREE DIFFERENT SITUATIONS, which this rule used to collapse into one.
    #
    # It asked only "does this unit have a panel that is IN the video", and
    # in_video requires the tick. Segments are born unticked, so on any
    # un-reviewed project EVERY unit failed and the rule fired once per unit —
    # nothing to do with quality. On the Overgeared lab runs that produced 52
    # findings for 52 units and 14 for 14, and the resulting "clean rows"
    # percentages were reproducible to the decimal as (panels - units)/panels.
    # The rule was measuring the unit count.
    #
    # What it should say depends on WHY the unit has no ticked panel:
    #   no segment at all      -> a real defect whoever is reviewing
    #   segments, none ticked, and nothing ticked anywhere -> the project has
    #                             simply not been reviewed yet; not a defect
    #   segments, none ticked, but OTHER units are ticked -> this unit was
    #                             deliberately or accidentally left out; worth
    #                             saying, but it is a review decision, not a
    #                             pipeline fault
    reviewed = any(r["timing"]["in_video"] for r in rows)
    units = {}
    for r in rows:
        u = r["placement"]["unit"]
        if u is None:
            continue
        slot = units.setdefault(u, {"sample": r, "has_segment": False,
                                    "in_video": False})
        if r["timing"].get("seg_index") is not None:
            slot["has_segment"] = True
        if r["timing"]["in_video"]:
            slot["in_video"] = True

    for unit in sorted(units):
        slot = units[unit]
        if slot["in_video"]:
            continue
        if not slot["has_segment"]:
            out.append(_finding(
                slot["sample"], "Script placement", "high",
                f"Narration unit {unit} has no panel at all — nothing was ever "
                "matched to it, so its line has no picture to play over.",
                "Assign a panel to this unit, or cut the line.",
                "rules", category="coverage"))
        elif reviewed:
            out.append(_finding(
                slot["sample"], "Script placement", "medium",
                f"Narration unit {unit} has panels, but none of them are ticked "
                "for the final video while other units are.",
                "Tick one of this unit's panels, or cut the line if leaving it "
                "out was deliberate.", "rules", confidence=0.7,
                category="coverage"))
        # else: the project is simply un-reviewed. Not a defect, and reporting
        # it once per unit is what made this rule useless.
    return out


# --------------------------------------------------------------- Claude
# This deployment's key has lived in Railway as CLAUDE_API_KEY since long
# before this module existed, so that name is checked FIRST and the SDK's own
# ANTHROPIC_API_KEY is the fallback. The key is passed explicitly rather than
# left to the SDK's env lookup, because the SDK only knows the second name —
# relying on it would have failed on the one deployment that matters.
API_KEY_VARS = ("CLAUDE_API_KEY", "ANTHROPIC_API_KEY")


def api_key():
    for name in API_KEY_VARS:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def _client():
    """Fail fast and by name. A validator that quietly skipped its Claude
    passes would report 'no problems found' on an unvalidated board, which is
    worse than reporting nothing."""
    key = api_key()
    if not key:
        raise ValidatorError(
            "No Claude API key found — set CLAUDE_API_KEY (or "
            "ANTHROPIC_API_KEY) in the Railway environment, never in code. "
            "The Claude review passes cannot run without it; rules-only mode "
            "still works.")
    try:
        import anthropic
    except ImportError as e:
        raise ValidatorError(
            "The 'anthropic' package is not installed on this server.") from e
    return anthropic.Anthropic(api_key=key)


# The SDK refuses a non-streaming request whose max_tokens implies it could run
# past the 10-minute HTTP timeout. The placement pass learned this the
# expensive way in production: it asked for 32000 tokens non-streaming and died
# with "Streaming is required for operations that may take longer than 10
# minutes" AFTER the describe and script stages had already been paid for.
# Anything at or above this streams instead.
STREAM_ABOVE_TOKENS = int(os.environ.get("VALIDATOR_STREAM_ABOVE", 8000))


def _call_claude(client, **kwargs):
    """One metered Claude call. Everything that reaches the API goes through
    here, so the gate cannot be bypassed by a new call site.

    Large requests are STREAMED and then collapsed back to a single message
    with .get_final_message(), so callers see exactly the same response object
    either way and no call site has to care which path it took.
    """
    model = kwargs.get("model", MODEL)
    stream = kwargs.pop("stream", None)
    if stream is None:
        stream = kwargs.get("max_tokens", 0) >= STREAM_ABOVE_TOKENS
    with usage.gate("claude", 1, model=model) as meter:
        if stream:
            with client.messages.stream(**kwargs) as s:
                resp = s.get_final_message()
        else:
            resp = client.messages.create(**kwargs)
        meter.from_anthropic(resp)
    cost = usage.token_cost(model, meter.prompt_tokens, meter.output_tokens)
    return resp, meter, cost


def _parse_json_reply(resp):
    """Structured outputs guarantee valid JSON in the first text block — but a
    refusal returns HTTP 200 with no usable content at all, so stop_reason is
    checked before content is read rather than after it explodes."""
    if getattr(resp, "stop_reason", None) == "refusal":
        det = getattr(resp, "stop_details", None)
        raise ValidatorError(
            "Claude declined to review this batch"
            + (f" ({getattr(det, 'category', None)})" if det else "")
            + ". The rows were left unvalidated rather than passed as clean.")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        raise ValidatorError("Claude returned no text block for this batch.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValidatorError(f"Claude returned unparseable JSON: {e}") from e


class _Tally:
    """Running cost/token totals for one pass."""

    def __init__(self):
        self.calls = 0
        self.cost = 0.0
        self.ptok = 0
        self.otok = 0

    def add(self, meter, cost):
        self.calls += 1
        self.cost += cost
        self.ptok += meter.prompt_tokens
        self.otok += meter.output_tokens

    def stats(self, **extra):
        d = {"calls": self.calls, "cost_usd": round(self.cost, 6),
             "prompt_tokens": self.ptok, "output_tokens": self.otok}
        d.update(extra)
        return d


# ------------------------------------------------------- pass A: chapter
CHAPTER_SYSTEM = """\
You are reading the narration script of one manhwa recap chapter, in order, and \
building a map of it that later checks will be judged against.

You are given the narration units in story order, each with the board rows \
assigned to it. Return a structural map of the chapter.

Scenes: group consecutive units that happen in one place and time, or form one \
continuous story movement. Give each a short title, the units it covers, and \
its phase in the chapter arc.

Reveals: a reveal is a point where the audience learns something they did not \
know — an identity, a betrayal, a power, a death, a relationship. Note the unit \
where each one LANDS. This matters because a recap that shows a reveal's image \
before the line that sets it up spoils itself.

Turns: points where the emotional register changes (hope to dread, defeat to \
triumph). These are where pacing errors hurt most.

Describe only what the narration actually says. Do not invent plot, and do not \
speculate about anything outside this chapter."""

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
                    "units": {"type": "array", "items": {"type": "integer"}},
                    "phase": {"type": "string", "enum": [
                        "setup", "escalation", "climax", "resolution"]},
                    "summary": {"type": "string"},
                },
                "required": ["scene", "title", "units", "phase", "summary"],
                "additionalProperties": False,
            },
        },
        "reveals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"unit": {"type": "integer"},
                               "what": {"type": "string"}},
                "required": ["unit", "what"],
                "additionalProperties": False,
            },
        },
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"unit": {"type": "integer"},
                               "turn": {"type": "string"}},
                "required": ["unit", "turn"],
                "additionalProperties": False,
            },
        },
        "chronology_notes": {"type": "string"},
    },
    "required": ["premise", "scenes", "reveals", "turns", "chronology_notes"],
    "additionalProperties": False,
}


def chapter_map(rows, model=MODEL, progress=None):
    """Pass A. One call. Everything downstream is judged against this, so it is
    built once, first, and then travels as a cacheable prefix."""
    client = _client()
    units = chapter_units(rows)
    if not units:
        raise ValidatorError("This chapter has no narration units to map.")
    if progress:
        progress(f"Reading the chapter ({len(units)} narration units)")

    payload = json.dumps(
        [{"unit": u["unit"], "text": u["text"][:1200],
          "rows": f"{u['rows'][0]}-{u['rows'][-1]}" if u["rows"] else ""}
         for u in units], ensure_ascii=False, indent=1)

    tally = _Tally()
    resp, meter, cost = _call_claude(
        client, model=model, max_tokens=16000,
        system=[{"type": "text", "text": CHAPTER_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT,
                       "format": {"type": "json_schema",
                                  "schema": CHAPTER_SCHEMA}},
        messages=[{"role": "user",
                   "content": "Map this chapter.\n\n" + payload}])
    tally.add(meter, cost)
    return _parse_json_reply(resp), tally.stats(units=len(units))


def _scene_of_unit(cmap, unit):
    for sc in (cmap or {}).get("scenes", []):
        if unit in (sc.get("units") or []):
            return sc
    return None


def _map_digest(cmap):
    """The chapter map as a compact brief. Kept small on purpose — sent with
    every window, so the full map would dominate each window's tokens."""
    lines = [f"PREMISE: {cmap.get('premise','')}"]
    for sc in cmap.get("scenes", []):
        lines.append(
            f"SCENE {sc.get('scene')} [{sc.get('phase')}] "
            f"units {sc.get('units')}: {sc.get('title')} — {sc.get('summary')}")
    for rv in cmap.get("reveals", []):
        lines.append(f"REVEAL lands at unit {rv.get('unit')}: {rv.get('what')}")
    for tn in cmap.get("turns", []):
        lines.append(f"TURN at unit {tn.get('unit')}: {tn.get('turn')}")
    if cmap.get("chronology_notes"):
        lines.append("CHRONOLOGY: " + cmap["chronology_notes"])
    return "\n".join(lines)


# ------------------------------------------------- pass B: sequence check
SEQUENCE_SYSTEM = """\
You are the accuracy backstop for an automated manhwa-recap pipeline, checking \
whether each panel carries the RIGHT narration AT THE RIGHT POINT in the \
chapter. A human reviews this board after you.

You are given a map of the whole chapter, then a window of consecutive board \
rows in reading order. Judge each row against its place in the chapter, not in \
isolation.

How the rows were produced, and therefore how they fail:
- Panel: a crop from the chapter, in reading order.
- System OCR: raw text read off the panel — dialogue, sound effects, sometimes \
scanlation watermarks.
- System description: an automated vision model's description of the panel.
- Script placement: which narration unit an automated matcher assigned. The \
matcher works on embedding similarity, so it fails by putting a line on a panel \
that is merely TOPICALLY RELATED rather than the panel the line is about. This \
is the most common and most damaging failure.

Report only these, with the matching category:

- "placement": the line on this row does not belong to this panel. If a panel \
in this window is plainly the one it belongs to, put that row number in \
target_row — that is what lets the reviewer fix it in one click. Use 0 when you \
cannot name one.
- "order": the line lands too early or too late for where the chapter is. This \
includes a reveal's panel appearing before the line that sets it up, and a \
reaction appearing before its cause.
- "continuity": the panel does not belong to the scene the surrounding rows are \
in — a different place, time or cast than the map says we are in here.
- "pacing": the rhythm here hurts the chapter — a climax beat rushed across \
panels that flash by, or a stall on one image through an emotional turn.

Set severity by how sure you are the row is WRONG, not by how much it matters:
- "high": definitely wrong, and you can point at the evidence.
- "medium": likely wrong; the reading is strong but not certain.
- "low": worth a human's eye; something is off but you cannot settle it.

Rules:
- A row you are unsure about is not a finding. Precision matters more than \
recall: a false alarm costs the human the exact reading time this is meant to \
save.
- Do NOT report arithmetic — flash panels, dead air, hold lengths, missing \
coverage, backwards unit numbers. Those are already checked deterministically \
before you see this, and repeating them is noise.
- Never invent panel content. Judge only from the OCR and description given.
- Style, tone and wording preferences are not findings.
- Panels legitimately left out (credit pages, duplicate shots) are correct \
behaviour, not findings.
- An empty findings list is a normal and frequent result."""

SEQ_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "row": {"type": "integer"},
                    "category": {"type": "string", "enum": [
                        "placement", "order", "continuity", "pacing"]},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "issue": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "confidence": {"type": "number"},
                    "target_row": {"type": "integer"},
                },
                "required": ["row", "category", "severity", "issue",
                             "suggestion", "confidence", "target_row"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def _row_for_prompt(r):
    """Only what a judgement needs. Panel ids and pixel dimensions would just
    be tokens Claude cannot use."""
    p, t = r["placement"], r["timing"]
    if p["role"] == "left_out":
        place = "LEFT OUT — no narration on this panel"
    else:
        place = (f"unit {p['unit']} ({p['role']}, panel {p.get('panel_slot')} "
                 f"of {p.get('panel_count')}): {p['text']}")
    return {
        "row": r["n"],
        "ocr": r["ocr"][:600] or "(none)",
        "description": r["desc"][:800] or "(none)",
        "script_placement": place,
        "on_screen_sec": t["dur"],
        "in_video": t["in_video"],
    }


def _windows(rows, size, overlap):
    """Consecutive windows that SHARE `overlap` rows with their neighbour, so
    no mismatch can fall through the seam between two windows."""
    size = max(1, size)
    step = max(1, size - max(0, overlap))
    out, i = [], 0
    while i < len(rows):
        out.append(rows[i:i + size])
        if i + size >= len(rows):
            break
        i += step
    return out


def sequence_findings(rows, cmap, model=MODEL, progress=None, accepted=()):
    """Pass B. Overlapping windows judged against the chapter map."""
    client = _client()
    by_n = {r["n"]: r for r in rows}
    brief = _map_digest(cmap)
    accepted_note = ""
    if accepted:
        accepted_note = ("\n\nThe reviewer has already accepted these rows as "
                         "intentional — do not report them again: "
                         + ", ".join(str(a) for a in sorted(accepted)))

    out, seen = [], set()
    wins = _windows(rows, BATCH_ROWS, SEQ_OVERLAP)
    tally = _Tally()

    for wi, win in enumerate(wins, start=1):
        if progress:
            progress(f"Sequence check {wi}/{len(wins)} "
                     f"(rows {win[0]['n']}–{win[-1]['n']})")
        payload = json.dumps([_row_for_prompt(r) for r in win],
                             ensure_ascii=False, indent=1)
        resp, meter, cost = _call_claude(
            client, model=model, max_tokens=16000,
            # Instructions and the chapter map are byte-identical on every
            # window, so they cache; only the rows after them change.
            system=[{"type": "text",
                     "text": SEQUENCE_SYSTEM + "\n\nCHAPTER MAP\n" + brief
                             + accepted_note,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT,
                           "format": {"type": "json_schema",
                                      "schema": SEQ_SCHEMA}},
            messages=[{"role": "user", "content":
                       "Check these rows in sequence.\n\n" + payload}])
        tally.add(meter, cost)

        for f in _parse_json_reply(resp).get("findings", []):
            r = by_n.get(f.get("row"))
            if r is None:          # a row number outside the window is a
                continue           # hallucination, not a finding
            cat = f.get("category") if f.get("category") in CATEGORIES \
                else "placement"
            fid = finding_id(r["panel_id"], cat)
            if fid in seen:        # overlapping windows see some rows twice
                continue
            seen.add(fid)
            sev = f.get("severity") if f.get("severity") in SEVERITIES \
                else "medium"
            tgt = f.get("target_row")
            unit = r["placement"]["unit"]
            sc = _scene_of_unit(cmap, unit) if unit is not None else None
            out.append(_finding(
                r, "Script placement", sev, f.get("issue", ""),
                f.get("suggestion", ""), "claude-sequence",
                confidence=f.get("confidence", 0.5), category=cat,
                target=tgt if isinstance(tgt, int) and tgt > 0 and tgt in by_n
                       else None,
                scene=sc.get("scene") if sc else None))

    return out, tally.stats(windows=len(wins), overlap=SEQ_OVERLAP)


# ------------------------------------------- pass D: description / OCR
DESC_SYSTEM = """\
You are checking whether the AUTOMATED DESCRIPTIONS on this board can be \
trusted. Every other check in this pipeline reasons from these descriptions, so \
a description that is wrong does not just cause one bad row — it poisons the \
match that was made from it, and every judgement made about that match.

For each row you are given the OCR text read off the panel, the automated \
description of it, and the descriptions of the rows immediately before and \
after.

Report a row when:
- "description": the description is likely WRONG about the panel. The strongest \
signals are a description that contradicts its own OCR (different characters, \
setting or action), and a description that breaks continuity with BOTH \
neighbours in a way the story does not explain.
- "ocr": the OCR text is likely garbage or belongs to something else — \
scanlation credits, a watermark, text bleeding in from an adjacent panel, or \
transcription so garbled it would mislead any match built on it.

Do not report a description merely for being short, plain or unexciting. Report \
it when you have a REASON to think it does not describe this panel.

Set severity by how sure you are: "high" definitely wrong, "medium" likely \
wrong, "low" worth a human's eye. An empty list is a normal result."""

DESC_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "row": {"type": "integer"},
                    "category": {"type": "string",
                                 "enum": ["description", "ocr"]},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "issue": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["row", "category", "severity", "issue",
                             "suggestion", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def description_findings(rows, model=MODEL, progress=None, skip_ids=()):
    """Pass D. The one pass aimed at the premise everything else stands on."""
    client = _client()
    by_n = {r["n"]: r for r in rows}
    out, seen = [], set(skip_ids)
    batches = [rows[i:i + BATCH_ROWS] for i in range(0, len(rows), BATCH_ROWS)]
    tally = _Tally()

    for bi, batch in enumerate(batches, start=1):
        if progress:
            progress(f"Description check {bi}/{len(batches)} "
                     f"(rows {batch[0]['n']}–{batch[-1]['n']})")
        items = []
        for r in batch:
            prev_r, next_r = by_n.get(r["n"] - 1), by_n.get(r["n"] + 1)
            items.append({
                "row": r["n"],
                "ocr": r["ocr"][:600] or "(none)",
                "description": r["desc"][:800] or "(none)",
                "prev_description": (prev_r["desc"][:300] if prev_r else ""),
                "next_description": (next_r["desc"][:300] if next_r else ""),
            })
        resp, meter, cost = _call_claude(
            client, model=model, max_tokens=16000,
            system=[{"type": "text", "text": DESC_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT,
                           "format": {"type": "json_schema",
                                      "schema": DESC_SCHEMA}},
            messages=[{"role": "user", "content":
                       "Check these descriptions.\n\n"
                       + json.dumps(items, ensure_ascii=False, indent=1)}])
        tally.add(meter, cost)

        for f in _parse_json_reply(resp).get("findings", []):
            r = by_n.get(f.get("row"))
            if r is None:
                continue
            cat = f.get("category") if f.get("category") in ("description", "ocr") \
                else "description"
            fid = finding_id(r["panel_id"], cat)
            if fid in seen:
                continue
            seen.add(fid)
            sev = f.get("severity") if f.get("severity") in SEVERITIES \
                else "medium"
            out.append(_finding(
                r, "System description" if cat == "description" else "System OCR",
                sev, f.get("issue", ""), f.get("suggestion", ""),
                "claude-description", confidence=f.get("confidence", 0.5),
                category=cat))

    return out, tally.stats(batches=len(batches))


# --------------------------------------------------------- pass E: vision
VISION_SYSTEM = """\
You are checking one storyboard row against the actual panel image, because \
every earlier check reasoned from an automated DESCRIPTION of this panel and \
that description may itself be wrong.

You are given the panel image, the text OCR read off it, the automated \
description, the narration line assigned to it, and the concern an earlier pass \
raised — or "routine spot check" when nothing was flagged and this row was \
sampled deliberately.

Decide:
- Is the automated description an accurate account of what is in the image?
- Does the assigned narration line belong to THIS panel?
- Is the earlier concern real?

Set verdict to "confirmed" when something is genuinely wrong with this row, \
"cleared" when the image shows the row is fine, and "unclear" when the image \
genuinely does not settle it. Clearing a false alarm is as valuable as \
confirming a real one — do not confirm to be safe. On a routine spot check, \
"cleared" is the expected answer and you should give it freely.

If the automated description is wrong, write what the panel actually shows in \
corrected_description. Leave it empty when the description is accurate."""

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["confirmed", "cleared", "unclear"]},
        "reason": {"type": "string"},
        "corrected_description": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["verdict", "reason", "corrected_description", "confidence"],
    "additionalProperties": False,
}


def _panel_image_block(pdir, row):
    """Downscaled JPEG. A 760x1598 crop tells Claude nothing a 1200px-tall one
    does not, and costs tokens for the privilege."""
    path = os.path.join(pdir, "crops", row["file"])
    if not os.path.exists(path):
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if max(im.size) > VISION_MAX_PX:
                scale = VISION_MAX_PX / float(max(im.size))
                im = im.resize((max(1, int(im.width * scale)),
                                max(1, int(im.height * scale))),
                               Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
    except OSError:
        return None
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.standard_b64encode(
                           buf.getvalue()).decode("ascii")}}


def spot_check_rows(rows, flagged, n=None):
    """Rows to open an image for even though NOTHING flagged them.

    This is the chain's only defence against its own blind spot: the sequence
    and description passes both reason from the description, so a description
    that is wrong AND plausible yields a row that looks clean to both. Sampling
    is stratified — evenly spaced across the chapter rather than random — so
    the sample cannot cluster in one scene and leave the rest unexamined.
    """
    n = SPOT_CHECK_ROWS if n is None else n
    pool = [r["n"] for r in rows
            if r["n"] not in flagged and r["timing"]["in_video"]]
    if n <= 0 or not pool:
        return []
    if len(pool) <= n:
        return pool
    step = len(pool) / float(n)
    return [pool[min(len(pool) - 1, int(i * step))] for i in range(n)]


def vision_findings(pdir, rows, targets, model=MODEL, progress=None, spot=()):
    """Pass E. Flagged rows first, then the spot-check sample, capped."""
    client = _client()
    by_n = {r["n"]: r for r in rows}
    checked, skipped = {}, []
    tally = _Tally()

    # The spot-check budget is reserved BEFORE flagged rows are queued, so a
    # chapter with many findings cannot crowd out the only check on the blind
    # spot. It never takes more than half the budget.
    flagged = sorted(targets)
    reserve = min(len(spot), MAX_VISION_ROWS // 2)
    ordered = flagged[:max(0, MAX_VISION_ROWS - reserve)]
    over = flagged[len(ordered):]
    sampled = [n for n in spot if n not in set(ordered)][
        :max(0, MAX_VISION_ROWS - len(ordered))]
    queue = [(n, False) for n in ordered] + [(n, True) for n in sampled]

    for i, (n, is_spot) in enumerate(queue, start=1):
        r = by_n.get(n)
        if r is None:
            continue
        img = _panel_image_block(pdir, r)
        if img is None:
            skipped.append({"row": n, "why": "panel image not readable"})
            continue
        if progress:
            progress(f"Image check {i}/{len(queue)} (row {n}"
                     + (", spot check)" if is_spot else ")"))

        if is_spot:
            concerns = ("routine spot check — nothing flagged this row; it was "
                        "sampled to test whether the automated descriptions can "
                        "be trusted")
        else:
            concerns = "; ".join(t["issue"] for t in targets[n]) or "general check"
        p = r["placement"]
        line = ("LEFT OUT — no narration" if p["role"] == "left_out"
                else f"unit {p['unit']}: {p['text']}")
        try:
            resp, meter, cost = _call_claude(
                client, model=model, max_tokens=4000,
                system=[{"type": "text", "text": VISION_SYSTEM,
                         "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": EFFORT,
                               "format": {"type": "json_schema",
                                          "schema": VISION_SCHEMA}},
                messages=[{"role": "user", "content": [
                    img,
                    {"type": "text", "text":
                     f"OCR: {r['ocr'][:600] or '(none)'}\n\n"
                     f"Automated description: {r['desc'][:800] or '(none)'}\n\n"
                     f"Narration assigned: {line}\n\n"
                     f"Concern raised earlier: {concerns}"},
                ]}])
        except ValidatorError as e:
            # One unusable reply must not cost the whole pass.
            skipped.append({"row": n, "why": str(e)})
            continue
        tally.add(meter, cost)
        try:
            v = _parse_json_reply(resp)
            v["spot_check"] = is_spot
            checked[n] = v
        except ValidatorError as e:
            skipped.append({"row": n, "why": str(e)})

    for n in over:
        skipped.append({"row": n, "why": f"past the {MAX_VISION_ROWS}-row "
                                         "image budget for this run"})

    return checked, skipped, tally.stats(
        flagged_checked=len(ordered), spot_checked=len(sampled))


# ------------------------------------------------------------- feedback
def feedback_path(pdir):
    return os.path.join(pdir, FEEDBACK_NAME)


def load_feedback(pdir):
    try:
        with open(feedback_path(pdir), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_feedback(pdir, fid, verdict, panel_id=None, category=None, note=""):
    """The lightweight feedback loop: a judgement the owner has already made is
    not re-litigated on the next run.

    'accepted' means the owner looked and decided the row is fine as it is —
    those findings come back demoted and marked, and their rows are named to the
    sequence pass so it stops raising them. 'fixed' and 'dismissed' are recorded
    the same way but kept distinct, because "I fixed it" and "I disagree" are
    different signals and averaging them would lose the only evidence of which
    findings were worth having.
    """
    if verdict not in ("accepted", "dismissed", "fixed", "reopened"):
        raise ValueError("verdict must be accepted, dismissed, fixed or reopened")
    fb = load_feedback(pdir)
    if verdict == "reopened":
        fb.pop(fid, None)
    else:
        fb[fid] = {"verdict": verdict, "panel_id": panel_id,
                   "category": category, "note": note or "",
                   "ts": datetime.now(timezone.utc).isoformat()}
    tmp = feedback_path(pdir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fb, f, indent=2)
    os.replace(tmp, feedback_path(pdir))
    return fb


# --------------------------------------------------------------- actions
def _actions_for(f, rows_by_n):
    """What the reviewer can DO about this finding, right here.

    Actions are attached per finding rather than offered as a generic menu,
    because the right move depends entirely on what is wrong: a poisoned
    description needs re-describing, a misplaced line needs swapping or moving,
    a credit page needs leaving out. Offering all of them everywhere would push
    the "which one applies?" decision back onto the reviewer, which is the work
    this is supposed to be removing.

    Every server action is executed by validator_actions.apply_action through
    the same storyboard_edit functions the board uses, so they are undoable and
    cannot drift from board state.
    """
    cat = f.get("category")
    si = f.get("seg_index")
    acts = [{"id": "goto", "label": "Go to row", "kind": "client"}]

    if cat in ("placement", "order", "continuity"):
        if f.get("target_row") and si is not None:
            tr = rows_by_n.get(f["target_row"])
            if tr:
                acts.append({
                    "id": "swap", "label": f"Swap to row {f['target_row']}",
                    "kind": "server",
                    "params": {"seg_index": si, "panel_id": tr["panel_id"]},
                    "preview": f"segment {si} plays over row {f['target_row']} "
                               f"({tr['panel_id']}) instead of this panel",
                })
        if si is not None:
            acts.append({"id": "move_earlier", "label": "Move line earlier",
                         "kind": "server", "params": {"seg_index": si},
                         "preview": "this line plays one slot earlier"})
            acts.append({"id": "move_later", "label": "Move line later",
                         "kind": "server", "params": {"seg_index": si},
                         "preview": "this line plays one slot later"})

    if cat in ("ocr", "pacing", "placement", "continuity"):
        acts.append({"id": "leave_out", "label": "Leave this panel out",
                     "kind": "server",
                     "params": {"panel_id": f.get("panel_id")},
                     "preview": "this panel is taken out of the final video"})

    if cat == "coverage":
        acts.append({"id": "put_back", "label": "Put this panel back in",
                     "kind": "server",
                     "params": {"panel_id": f.get("panel_id")},
                     "preview": "this panel returns to the final video"})

    if cat in ("description", "ocr"):
        acts.append({"id": "redescribe", "label": "Re-run description",
                     "kind": "server",
                     "params": {"panel_id": f.get("panel_id")},
                     "preview": "the vision model looks at this panel again"})
        acts.append({"id": "reocr", "label": "Re-run OCR",
                     "kind": "server",
                     "params": {"panel_id": f.get("panel_id")},
                     "preview": "only the OCR text is re-read"})

    if cat == "pacing" and si is not None:
        acts.append({"id": "use_full_panel", "label": "Use full panel",
                     "kind": "server", "params": {"seg_index": si},
                     "preview": "the crop is replaced by the whole panel"})

    if si is not None:
        acts.append({"id": "send_to_review", "label": "Send to review",
                     "kind": "server", "params": {"seg_index": si},
                     "preview": "flagged for a decision on the Review page"})

    acts.append({"id": "accept", "label": "Accept as intentional",
                 "kind": "server",
                 "params": {"finding_id": f.get("id"),
                            "panel_id": f.get("panel_id"),
                            "category": cat},
                 "preview": "this finding stops being raised on future runs"})
    acts.append({"id": "jump_to_source", "label": "Open panel image",
                 "kind": "client"})
    return acts


# --------------------------------------------------------------- driver
def _targets_for_vision(findings):
    """Rows worth opening the image for: the ones whose finding depends on the
    description being right. A pacing finding is arithmetic — the image cannot
    change the answer, so it is not worth an image call."""
    wanted = ("placement", "description", "order", "continuity")
    out = {}
    for f in findings:
        if f["category"] in wanted and f["row"] is not None:
            out.setdefault(f["row"], []).append(f)
    return out


def report_path(pdir):
    return os.path.join(pdir, REPORT_NAME)


def load_report(pdir):
    try:
        with open(report_path(pdir), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_report(pdir, report):
    tmp = report_path(pdir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    os.replace(tmp, report_path(pdir))
    return report


def validate(pdir, review=None, mode="full", model=None, progress=None):
    """Run the chain and persist the report.

    mode:
      'rules'  — rule pass only. Free, no credentials, instant.
      'text'   — rules + chapter map + sequence + description/OCR.
      'full'   — adds the vision pass, including spot checks (default).

    Always returns a report. A failure in a Claude pass is recorded IN the
    report with the findings gathered so far, rather than throwing away work the
    owner could still use — but `status` says 'error' so nothing reads a partial
    run as a clean board.
    """
    model = model or MODEL
    started = time.time()
    rows = build_rows(pdir, review)
    rows_by_n = {r["n"]: r for r in rows}
    feedback = load_feedback(pdir)

    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "model": model,
        "effort": EFFORT,
        "rows": len(rows),
        "status": "ok",
        "error": None,
        "passes": {"rules": {}, "chapter_map": {}, "sequence": {},
                   "description": {}, "vision": {}},
        "chapter": None,
        "findings": [],
        "accepted_suppressed": 0,
        "cost_usd": 0.0,
        "calls": 0,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "vision_skipped": [],
    }
    if not rows:
        report["error"] = "This project has no panels to validate yet."
        report["status"] = "error"
        return save_report(pdir, report)

    def _absorb(stats):
        report["cost_usd"] = round(report["cost_usd"] + stats["cost_usd"], 6)
        report["calls"] += stats["calls"]
        report["prompt_tokens"] += stats["prompt_tokens"]
        report["output_tokens"] += stats["output_tokens"]

    # ---- rules ---------------------------------------------------------
    if progress:
        progress("Rule checks (timing, coverage, ordering, credit pages)")
    findings = rule_findings(rows)
    report["passes"]["rules"] = {"findings": len(findings), "cost_usd": 0.0}

    def _finish(err=None):
        if err:
            report["status"] = "error"
            report["error"] = str(err)

        # Previously accepted findings are DEMOTED and marked, never dropped. A
        # finding that vanishes because it was accepted once is a finding the
        # owner can never reconsider, and the board would look cleaner than it is.
        suppressed = 0
        for f in findings:
            fb = feedback.get(f["id"])
            if not fb:
                continue
            f["feedback"] = fb["verdict"]
            if fb["verdict"] in ("accepted", "fixed"):
                f["severity"] = "low"
                f["severity_label"] = SEVERITY_LABELS["low"]
                f["accepted"] = True
                suppressed += 1
        report["accepted_suppressed"] = suppressed

        for f in findings:
            f["actions"] = _actions_for(f, rows_by_n)
        findings.sort(key=lambda f: (SEVERITIES.index(f["severity"]),
                                     -f.get("confidence", 0), f["row"]))
        report["findings"] = findings
        report["counts"] = {s: sum(1 for f in findings
                                   if f["severity"] == s and not f.get("accepted"))
                            for s in SEVERITIES}
        report["by_category"] = {
            c: sum(1 for f in findings
                   if f["category"] == c and not f.get("accepted"))
            for c in CATEGORIES}
        report["clean_rows"] = len(rows) - len({f["row"] for f in findings})
        report["elapsed_sec"] = round(time.time() - started, 1)
        return save_report(pdir, report)

    if mode == "rules":
        return _finish()

    # ---- pass A: chapter map -------------------------------------------
    try:
        cmap, stats = chapter_map(rows, model=model, progress=progress)
    except (ValidatorError, usage.UsageCapExceeded) as e:
        return _finish(e)
    report["passes"]["chapter_map"] = stats
    report["chapter"] = cmap
    _absorb(stats)

    # ---- pass B: sequence ----------------------------------------------
    accepted_rows = {r["n"] for r in rows
                     if any(feedback.get(finding_id(r["panel_id"], c), {})
                            .get("verdict") in ("accepted", "fixed")
                            for c in CATEGORIES)}
    try:
        seq, stats = sequence_findings(rows, cmap, model=model,
                                       progress=progress,
                                       accepted=accepted_rows)
    except (ValidatorError, usage.UsageCapExceeded) as e:
        return _finish(e)
    findings.extend(seq)
    report["passes"]["sequence"] = stats
    _absorb(stats)

    # ---- pass D: description / OCR --------------------------------------
    try:
        dsc, stats = description_findings(
            rows, model=model, progress=progress,
            skip_ids={f["id"] for f in findings})
    except (ValidatorError, usage.UsageCapExceeded) as e:
        return _finish(e)
    findings.extend(dsc)
    report["passes"]["description"] = stats
    _absorb(stats)

    if mode == "text":
        return _finish()

    # ---- pass E: vision --------------------------------------------------
    targets = _targets_for_vision(findings)
    spot = spot_check_rows(rows, {f["row"] for f in findings})
    if not targets and not spot:
        report["passes"]["vision"] = {
            "calls": 0, "cost_usd": 0.0, "prompt_tokens": 0, "output_tokens": 0,
            "note": "nothing flagged and no rows available to spot check"}
        return _finish()

    try:
        checked, skipped, vstats = vision_findings(
            pdir, rows, targets, model=model, progress=progress, spot=spot)
    except (ValidatorError, usage.UsageCapExceeded) as e:
        return _finish(e)

    report["passes"]["vision"] = vstats
    _absorb(vstats)
    report["vision_skipped"] = skipped

    # Fold the verdicts back in. A cleared finding is DEMOTED and annotated,
    # never deleted — the owner needs to see that a check ran and disagreed,
    # otherwise the vision pass is invisible when it does its best work.
    for f in findings:
        v = checked.get(f["row"])
        if not v or v.get("spot_check"):
            continue
        f["vision"] = {"verdict": v.get("verdict"),
                       "reason": v.get("reason", ""),
                       "corrected_description": v.get("corrected_description", "")}
        if v.get("verdict") == "cleared":
            f["severity"] = "low"
            f["severity_label"] = SEVERITY_LABELS["low"]
            f["cleared"] = True
        elif v.get("verdict") == "confirmed":
            f["confidence"] = max(f.get("confidence", 0.5),
                                  float(v.get("confidence", 0.8) or 0.8))
            f["confirmed"] = True

    # A spot check that CONFIRMS is the chain catching its own blind spot: a row
    # every text pass thought was clean, which the image says is not. Those
    # become findings in their own right — they are the whole reason the sample
    # exists, so they must not be dropped for having no prior finding to attach
    # to.
    for n, v in checked.items():
        if not v.get("spot_check") or v.get("verdict") != "confirmed":
            continue
        r = rows_by_n.get(n)
        if not r:
            continue
        f = _finding(
            r, "System description", "medium",
            "Spot check: " + (v.get("reason")
                              or "the image does not match this row."),
            "Nothing flagged this row — the image check found it. Re-run the "
            "description, then re-check the line placed here.",
            "claude-vision-spot",
            confidence=float(v.get("confidence", 0.6) or 0.6),
            category="description")
        f["vision"] = {"verdict": "confirmed", "reason": v.get("reason", ""),
                       "corrected_description": v.get("corrected_description", "")}
        f["from_spot_check"] = True
        findings.append(f)

    report["passes"]["vision"]["spot_confirmed"] = sum(
        1 for v in checked.values()
        if v.get("spot_check") and v.get("verdict") == "confirmed")

    return _finish()
