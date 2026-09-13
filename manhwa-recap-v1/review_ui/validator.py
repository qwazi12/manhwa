"""Board validator — the review chain that runs BEFORE the owner's eyes.

The storyboard is assembled by machines that are not, and will never be, 100%
accurate: a splitter cuts panels, an OCR pass reads text off them, a vision
model describes them, a matcher decides which narration line lands on which
panel, and a segmenter decides how long each one is on screen. Every one of
those can be wrong, and until now the ONLY thing that caught a wrong one was
the owner reading 138 rows by hand.

This module is the second and third link in the chain:

    splitter/OCR/vision/matcher  ->  RULES  ->  CLAUDE (text)  ->  CLAUDE (vision)  ->  owner
                                     pass 1     pass 2            pass 3

Pass 1 — RULES (free, deterministic, always runs).
    Arithmetic and pattern checks have exact answers, so they are computed
    here rather than paid for: subliminal one-frame panels, dead air,
    story-order inversions, scanlation credit pages, missing descriptions,
    narration units that never made it onto a panel. An LLM asked to do
    arithmetic is slower, costlier and WRONG more often than `<`.

Pass 2 — CLAUDE, text only (batched).
    The judgement call rules cannot make: does the narration line assigned to
    this panel actually belong to this panel, given what the OCR read and what
    the vision model saw? This is the failure the owner has been fixing by
    hand, and it is semantic, not arithmetic.

Pass 3 — CLAUDE, vision (only on panels already flagged).
    Passes 1 and 2 both reason about a DESCRIPTION of the panel. If that
    description is itself wrong, both are reasoning from a bad premise. Pass 3
    opens the actual image for the flagged rows only, and either confirms the
    finding or clears it — so a wrong description gets caught by the one pass
    that is not downstream of it.

Cost: every Claude call goes through usage.gate('claude', 1, model=...), so it
is capped by the same per-job and daily-spend guardrails as Gemini and TTS,
metered from the tokens the API actually reports, and shows up in the same
cost header on the site. There is no path in this file that reaches the API
without passing that gate.

Pass 1 needs no credentials and always runs. Passes 2 and 3 raise a clear
error naming ANTHROPIC_API_KEY when it is missing, rather than degrading into
a validation that silently checked less than it claimed to.
"""

import base64
import io
import json
import os
import re
import time
from datetime import datetime, timezone

import usage

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_NAME = "validation.json"

# ---------------------------------------------------------------- config
# Opus is the default because this job IS the accuracy backstop — the whole
# point is catching what a cheaper model already missed. Override to
# claude-sonnet-5 or claude-haiku-4-5 to trade accuracy for cost; the rate card
# in usage.py prices all three.
MODEL = os.environ.get("VALIDATOR_MODEL", "claude-opus-5")

# Effort controls how hard Claude thinks per batch. 'medium' is the default
# because pass 1 has already removed the mechanical findings, leaving Claude a
# narrower question. Raise to 'high' for a final pre-publish sweep.
EFFORT = os.environ.get("VALIDATOR_EFFORT", "medium")

# Rows per text-pass call. Large enough that Claude can see story flow across
# neighbouring panels (which is how an out-of-place line becomes obvious), small
# enough that one refusal or malformed reply costs one batch, not the chapter.
BATCH_ROWS = int(os.environ.get("VALIDATOR_BATCH_ROWS", 25))

# Hard ceiling on pass 3. Vision calls carry an image each and are the
# expensive ones; flagging half a chapter must not quietly become 70 image
# calls. Rows beyond the cap keep their pass-2 finding, unconfirmed, and the
# report says so rather than pretending they were checked.
MAX_VISION_ROWS = int(os.environ.get("VALIDATOR_MAX_VISION_ROWS", 24))

# Panel crops run to 760x1598 and larger. Anything past ~1200px on the long
# edge costs tokens without telling Claude anything new about a comic panel.
VISION_MAX_PX = int(os.environ.get("VALIDATOR_VISION_MAX_PX", 1200))

# Timing thresholds for the rule pass.
#
# NOTE ON A CHECK THAT IS DELIBERATELY ABSENT: "can the narration be said in
# the time available" is NOT checkable here, because it cannot fail. The
# timeline is DERIVED from the TTS audio — beats carry the real aligned
# windows and segments are fitted to them — so words always fit by
# construction. An earlier version of this file checked words-per-second and
# raised 27 false alarms on a clean chapter, because folded panels all carry a
# copy of their shared beat's full text: six panels sharing one 24-word beat
# each looked like they had to speak 24 words in their own slice. The failure
# modes below are the ones that CAN actually happen.
#
# A panel under FLASH_SEC is on screen too briefly to register as an image.
FLASH_SEC = float(os.environ.get("VALIDATOR_FLASH_SEC", 1.0))
# A panel held this long with no narration over it is dead air.
LONG_SILENT_HOLD_SEC = float(os.environ.get("VALIDATOR_LONG_HOLD_SEC", 12.0))

SEVERITIES = ("high", "medium", "low")

# Scanlation groups stamp credit pages with their own name, a chapter number
# and a Discord/site URL. Those pages are never story content, and a narration
# line landing on one is always wrong.
_CREDIT_MARKERS = re.compile(
    r"(scans?\b|scanlation|discord\.gg|\.com\b|\.net\b|telegram|patreon|"
    r"translator|proofread|redraw|typeset|\bTL\b|\bPR\b|\bQC\b)",
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
    reads: Panel, System OCR, System description, Script placement, On-screen
    timing & motion.

    Deliberately built from the same JSON the storyboard renders from, not by
    scraping the rendered HTML — the validator has to judge the DATA, so that a
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
                "seg_index": seg.get("seg_index") if seg else None,
                # Total time this PANEL is on screen, across every segment that
                # shows it — not just the first one.
                "dur": round(sum(float(x.get("dur", 0.0)) for x in mine), 2)
                       if mine else None,
                "start": round(float(seg.get("start", 0.0)), 2) if seg else None,
                "in_video": in_video,
                "spoken_words": _words(spoken),
                "silent": bool(seg and not (seg.get("beats") or [])),
            },
        })
    return rows


# --------------------------------------------------------------- pass 1
def _finding(row, column, severity, issue, suggestion, source, confidence=1.0):
    return {
        "row": row.get("n"),
        "panel_id": row.get("panel_id"),
        "column": column,
        "severity": severity,
        "issue": issue,
        "suggestion": suggestion,
        "source": source,
        "confidence": round(float(confidence), 2),
    }


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
                "match on, so any line placed here was placed blind.", "rules"))
        elif not r["desc_ok"]:
            out.append(_finding(
                r, "System description", "medium",
                "The vision pass marked this description as not ok.",
                "Re-run describe for this panel before trusting its placement.",
                "rules"))

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
                    "rules"))

        # --- On-screen timing & motion ------------------------------------
        if t["in_video"] and t["dur"]:
            if t["dur"] < FLASH_SEC:
                out.append(_finding(
                    r, "On-screen timing & motion", "medium",
                    f"On screen for only {t['dur']}s — too brief to read as a "
                    "panel; it will register as a flicker.",
                    "Drop this panel from the cut, or take time from a "
                    "neighbouring panel in the same narration unit.", "rules"))
            elif t["silent"] and t["dur"] >= LONG_SILENT_HOLD_SEC:
                out.append(_finding(
                    r, "On-screen timing & motion", "medium",
                    f"{t['dur']}s on screen with no narration over it.",
                    "Shorten the hold or move a line onto it — this reads as "
                    "dead air.", "rules"))

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
                    "rules"))
            last_unit = max(last_unit, p["unit"]) if last_unit is not None else p["unit"]

    # --- Coverage: a unit that never reached the video at all -------------
    placed = {r["placement"]["unit"] for r in rows
              if r["placement"]["unit"] is not None and r["timing"]["in_video"]}
    authored = {r["placement"]["unit"] for r in rows
                if r["placement"]["unit"] is not None}
    for unit in sorted(authored - placed):
        sample = next((r for r in rows if r["placement"]["unit"] == unit), None)
        if sample:
            out.append(_finding(
                sample, "Script placement", "high",
                f"Narration unit {unit} has no panel in the final video — "
                "its line will be heard over someone else's panel or not at all.",
                "Tick a panel for this unit, or cut the line.", "rules"))
    return out


# --------------------------------------------------------------- Claude
def _client():
    """Fail fast and by name. A validator that quietly skipped its Claude
    passes would report 'no problems found' on an unvalidated board, which is
    worse than reporting nothing."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ValidatorError(
            "ANTHROPIC_API_KEY is not set — the Claude review passes cannot "
            "run. Set it in the Railway environment (never in code), or run "
            "the validator in rules-only mode.")
    try:
        import anthropic
    except ImportError as e:
        raise ValidatorError(
            "The 'anthropic' package is not installed on this server.") from e
    return anthropic.Anthropic()


SYSTEM_TEXT = """\
You are the accuracy backstop for an automated manhwa-recap pipeline. A human \
reviews this board after you. Your job is to catch what the machines got wrong \
so the human does not have to find it by reading every row.

How each row was produced, and therefore how it fails:
- Panel: a crop from the chapter, in reading order.
- System OCR: raw text read off the panel. Contains dialogue and sound effects, \
and sometimes scanlation watermarks or credits.
- System description: an automated vision model's description of the panel. It \
is usually right but sometimes generic, and occasionally describes a different \
panel entirely.
- Script placement: which narration unit an automated matcher assigned to this \
panel. The matcher works on embedding similarity, so it fails by putting a line \
on a panel that is merely topically related rather than the panel the line is \
actually about. THIS IS THE MOST COMMON AND MOST DAMAGING FAILURE.
- On-screen timing: how long the panel is shown in the rendered video.

Report ONLY these, and only when you are confident from the evidence in the row:
1. A narration line placed on a panel it does not describe, when a nearby panel \
in the batch is plainly the one it belongs to. Name that panel in your suggestion.
2. A description that contradicts the OCR on the same panel (they describe \
different scenes or different characters).
3. A description too generic to have driven a real match ("a character stands", \
"a dramatic scene") on a panel carrying narration.
4. OCR that is scanlation credits or a watermark on a panel that still carries \
narration.

Arithmetic checks (speech speed, hold length, story-order inversions, missing \
coverage) are already done deterministically before you see this. Do not repeat \
them and do not comment on timing numbers.

Rules:
- A row you are unsure about is not a finding. Precision matters more than \
recall here: a false alarm costs the human the exact reading time this is meant \
to save.
- Never invent panel content. Judge only from the OCR and description given.
- Style, tone and wording preferences are not findings. Only factual mismatches.
- Panels legitimately left out (credit pages, duplicate shots) are correct \
behaviour, not findings.
- Return an empty findings list when the batch is clean. That is a normal and \
frequent result."""

FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "row": {"type": "integer"},
                    "column": {"type": "string", "enum": [
                        "Script placement", "System description", "System OCR"]},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "issue": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["row", "column", "severity", "issue",
                             "suggestion", "confidence"],
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
        "in_video": t["in_video"],
    }


def _call_claude(client, **kwargs):
    """One metered Claude call. Everything that reaches the API goes through
    here, so the gate cannot be bypassed by a new call site."""
    model = kwargs.get("model", MODEL)
    with usage.gate("claude", 1, model=model) as meter:
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


def claude_text_findings(rows, model=MODEL, progress=None):
    """Pass 2. Batched so Claude sees neighbouring panels — an out-of-place
    line is recognisable mostly by the fact that the panel it belongs to is
    sitting two rows away."""
    client = _client()
    by_n = {r["n"]: r for r in rows}
    out, calls, cost, ptok, otok = [], 0, 0.0, 0, 0

    batches = [rows[i:i + BATCH_ROWS] for i in range(0, len(rows), BATCH_ROWS)]
    for bi, batch in enumerate(batches, start=1):
        if progress:
            progress(f"Claude review {bi}/{len(batches)} "
                     f"(rows {batch[0]['n']}–{batch[-1]['n']})")
        payload = json.dumps([_row_for_prompt(r) for r in batch],
                             ensure_ascii=False, indent=1)
        resp, meter, c = _call_claude(
            client,
            model=model,
            max_tokens=16000,
            # The instructions are byte-identical on every batch, so they are
            # a cacheable prefix; only the rows after it change.
            system=[{"type": "text", "text": SYSTEM_TEXT,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT,
                           "format": {"type": "json_schema",
                                      "schema": FINDINGS_SCHEMA}},
            messages=[{"role": "user", "content":
                       "Review these storyboard rows.\n\n" + payload}],
        )
        calls += 1
        cost += c
        ptok += meter.prompt_tokens
        otok += meter.output_tokens

        for f in _parse_json_reply(resp).get("findings", []):
            r = by_n.get(f.get("row"))
            if r is None:          # a row number outside the batch is a
                continue           # hallucination, not a finding
            out.append(_finding(
                r, f.get("column", "Script placement"),
                f.get("severity", "medium") if f.get("severity") in SEVERITIES
                else "medium",
                f.get("issue", ""), f.get("suggestion", ""),
                "claude-text", f.get("confidence", 0.5)))

    return out, {"calls": calls, "cost_usd": round(cost, 6),
                 "prompt_tokens": ptok, "output_tokens": otok,
                 "batches": len(batches)}


# --------------------------------------------------------- pass 3: vision
VISION_SYSTEM = """\
You are checking one storyboard row against the actual panel image, because \
every earlier check reasoned from an automated DESCRIPTION of this panel and \
that description may itself be wrong.

You are given the panel image, the text OCR read off it, the automated \
description, the narration line assigned to it, and the concern an earlier \
pass raised.

Decide:
- Is the automated description an accurate account of what is in the image?
- Does the assigned narration line belong to THIS panel?
- Is the earlier concern real?

Set verdict to "confirmed" when the concern is real, "cleared" when the image \
shows the row is actually fine, and "unclear" when the image genuinely does not \
settle it. Clearing a false alarm is as valuable as confirming a real one — do \
not confirm to be safe.

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


def claude_vision_findings(pdir, rows, targets, model=MODEL, progress=None):
    """Pass 3. Runs ONLY on rows an earlier pass flagged, capped, and reports
    honestly about the ones it could not reach."""
    client = _client()
    by_n = {r["n"]: r for r in rows}
    checked, calls, cost, ptok, otok = {}, 0, 0.0, 0, 0
    skipped = []

    ordered = sorted(targets)[:MAX_VISION_ROWS]
    over = sorted(targets)[MAX_VISION_ROWS:]

    for i, n in enumerate(ordered, start=1):
        r = by_n.get(n)
        if r is None:
            continue
        img = _panel_image_block(pdir, r)
        if img is None:
            skipped.append({"row": n, "why": "panel image not readable"})
            continue
        if progress:
            progress(f"Claude vision check {i}/{len(ordered)} (row {n})")

        concerns = "; ".join(t["issue"] for t in targets[n]) or "general check"
        p = r["placement"]
        line = ("LEFT OUT — no narration" if p["role"] == "left_out"
                else f"unit {p['unit']}: {p['text']}")
        try:
            resp, meter, c = _call_claude(
                client,
                model=model,
                max_tokens=4000,
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
                ]}],
            )
        except ValidatorError as e:
            # One unusable reply must not cost the whole pass.
            skipped.append({"row": n, "why": str(e)})
            continue
        calls += 1
        cost += c
        ptok += meter.prompt_tokens
        otok += meter.output_tokens
        try:
            checked[n] = _parse_json_reply(resp)
        except ValidatorError as e:
            skipped.append({"row": n, "why": str(e)})

    for n in over:
        skipped.append({"row": n, "why": f"past the {MAX_VISION_ROWS}-row "
                                         "vision cap for this run"})

    return checked, skipped, {"calls": calls, "cost_usd": round(cost, 6),
                              "prompt_tokens": ptok, "output_tokens": otok}


# --------------------------------------------------------------- driver
def _targets_for_vision(findings):
    """Rows worth opening the image for: the ones whose finding depends on the
    description being right. A timing finding is arithmetic — the image cannot
    change the answer, so it is not worth an image call."""
    wanted = ("Script placement", "System description")
    out = {}
    for f in findings:
        if f["column"] in wanted and f["row"] is not None:
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
      'rules'  — pass 1 only. Free, no credentials, instant.
      'text'   — passes 1 and 2.
      'full'   — passes 1, 2 and 3 (default).

    Always returns a report. A failure in a Claude pass is recorded IN the
    report with the findings gathered so far, rather than throwing away work
    the owner could still use — but `status` says 'error' so nothing reads a
    partial run as a clean board.
    """
    model = model or MODEL
    started = time.time()
    rows = build_rows(pdir, review)

    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "model": model,
        "effort": EFFORT,
        "rows": len(rows),
        "status": "ok",
        "error": None,
        "passes": {"rules": {}, "claude_text": {}, "claude_vision": {}},
        "findings": [],
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

    # ---- pass 1 -------------------------------------------------------
    if progress:
        progress("Rule checks (timing, coverage, ordering, credit pages)")
    findings = rule_findings(rows)
    report["passes"]["rules"] = {"findings": len(findings), "cost_usd": 0.0}

    def _finish(err=None):
        if err:
            report["status"] = "error"
            report["error"] = str(err)
        findings.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["row"]))
        report["findings"] = findings
        report["counts"] = {s: sum(1 for f in findings if f["severity"] == s)
                            for s in SEVERITIES}
        report["clean_rows"] = len(rows) - len({f["row"] for f in findings})
        report["elapsed_sec"] = round(time.time() - started, 1)
        return save_report(pdir, report)

    if mode == "rules":
        return _finish()

    # ---- pass 2 -------------------------------------------------------
    try:
        text_found, stats = claude_text_findings(rows, model=model,
                                                 progress=progress)
    except (ValidatorError, usage.UsageCapExceeded) as e:
        return _finish(e)
    findings.extend(text_found)
    report["passes"]["claude_text"] = stats
    report["cost_usd"] = round(report["cost_usd"] + stats["cost_usd"], 6)
    report["calls"] += stats["calls"]
    report["prompt_tokens"] += stats["prompt_tokens"]
    report["output_tokens"] += stats["output_tokens"]

    if mode == "text":
        return _finish()

    # ---- pass 3 -------------------------------------------------------
    targets = _targets_for_vision(findings)
    if not targets:
        report["passes"]["claude_vision"] = {
            "calls": 0, "cost_usd": 0.0, "prompt_tokens": 0,
            "output_tokens": 0, "note": "nothing flagged that an image could settle"}
        return _finish()

    try:
        checked, skipped, vstats = claude_vision_findings(
            pdir, rows, targets, model=model, progress=progress)
    except (ValidatorError, usage.UsageCapExceeded) as e:
        return _finish(e)

    report["passes"]["claude_vision"] = vstats
    report["cost_usd"] = round(report["cost_usd"] + vstats["cost_usd"], 6)
    report["calls"] += vstats["calls"]
    report["prompt_tokens"] += vstats["prompt_tokens"]
    report["output_tokens"] += vstats["output_tokens"]
    report["vision_skipped"] = skipped

    # Fold the verdicts back in. A cleared finding is DEMOTED and annotated,
    # never deleted — the owner needs to be able to see that a check ran and
    # disagreed, otherwise pass 3 is invisible when it does its best work.
    for f in findings:
        v = checked.get(f["row"])
        if not v or f["column"] not in ("Script placement", "System description"):
            continue
        f["vision"] = {"verdict": v.get("verdict"),
                       "reason": v.get("reason", ""),
                       "corrected_description": v.get("corrected_description", "")}
        if v.get("verdict") == "cleared":
            f["severity"] = "low"
            f["cleared"] = True
        elif v.get("verdict") == "confirmed":
            f["confidence"] = max(f.get("confidence", 0.5),
                                  float(v.get("confidence", 0.8) or 0.8))
            f["confirmed"] = True

    return _finish()
