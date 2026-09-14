"""EXPERIMENT — the non-audio pipeline driven by Claude instead of Gemini.

This is a SIDE EXPERIMENT, not a replacement. Nothing in here is wired into
ingest, render or export, and nothing it writes lands in the files the board
reads. It exists so the owner can answer one question with evidence instead of
impressions: on the same chapter, with the same panel crops, does Claude make
better decisions than the current Gemini chain?

WHAT IT REPLACES (the board's five columns, minus audio):

    Panel                    -> crop box chosen by Claude from the panel art
    System OCR               -> Claude reads the text off the panel
    System description       -> Claude describes the panel
    Script placement         -> Claude writes the narration AND assigns it
    On-screen timing & motion-> Claude proposes duration + camera move

WHAT IT DELIBERATELY DOES NOT TOUCH:

  - Audio. No TTS, no narration audio, no beat alignment. The owner asked for
    the non-audio chain only, and the timeline that exists in production is
    DERIVED from real TTS audio — so this variant proposes timings rather than
    pretending to have measured them. Every comparison of the timing column has
    to be read in that light, and `timing_is_proposed` in the manifest says so.
  - Scraping and panel splitting. Those are shared infrastructure, not Gemini
    decisions, so both variants work from the SAME crops. That is what makes
    the comparison fair: the only variable is who made the judgement calls.

ISOLATION: everything is written under <project>/claude_test/. The production
descriptions.json, script.json and segments.json are never opened for writing
by this module. Reverting the experiment is `rm -rf claude_test`.

COST: every call goes through usage.gate('claude', 1, model=...), the same gate
as the Check passes, so the experiment is capped by the same daily spend limit
and shows up in the same header. Panels are batched several images to a call —
a 138-panel chapter is ~23 vision calls, not 138.
"""

import base64
import io
import json
import os
import re
import time
from datetime import datetime, timezone

import usage
import validator          # reuse its client, gate wrapper and reply parsing

OUT_DIR = "claude_test"

# The experiment is the accuracy question, so it runs the strong model by
# default — measuring a cheap model would answer a different question.
MODEL = os.environ.get("CLAUDE_TEST_MODEL", "claude-opus-5")
EFFORT = os.environ.get("CLAUDE_TEST_EFFORT", "medium")

# Panels per vision call. Several images in one call keeps the chapter
# affordable; too many and the model starts losing track of which image is
# which, which is the one error that would poison every downstream comparison.
PANELS_PER_CALL = int(os.environ.get("CLAUDE_TEST_PANELS_PER_CALL", 6))

# Panels sent per scripting/placement call.
SCRIPT_CHUNK = int(os.environ.get("CLAUDE_TEST_SCRIPT_CHUNK", 40))

# A hard stop so a mis-click cannot spend a chapter's worth of Opus vision.
MAX_PANELS = int(os.environ.get("CLAUDE_TEST_MAX_PANELS", 200))

VISION_MAX_PX = int(os.environ.get("CLAUDE_TEST_VISION_MAX_PX", 1200))

STAGES = ("describe", "script", "place", "timing")


class PipelineError(RuntimeError):
    """A condition the operator must see — not a finding about the output."""


# ------------------------------------------------------------------ io
def out_dir(pdir):
    return os.path.join(pdir, OUT_DIR)


def _write(pdir, name, data):
    d = out_dir(pdir)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, os.path.join(d, name))
    return os.path.join(d, name)


def read(pdir, name, default=None):
    try:
        with open(os.path.join(out_dir(pdir), name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _natural(pid):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", pid or "")]


def baseline_panels(pdir):
    """The panel list, in reading order, taken from the SAME descriptions.json
    the production board uses — so both variants describe an identical set of
    crops in an identical order."""
    try:
        with open(os.path.join(pdir, "descriptions.json"), encoding="utf-8") as f:
            descs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    descs.sort(key=lambda r: _natural(r.get("panel_id", "")))
    return descs


def _image_block(pdir, rec):
    path = os.path.join(pdir, "crops", rec.get("file") or f"{rec['panel_id']}.png")
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
                sc = VISION_MAX_PX / float(max(im.size))
                im = im.resize((max(1, int(im.width * sc)),
                                max(1, int(im.height * sc))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
    except OSError:
        return None
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.standard_b64encode(
                           buf.getvalue()).decode("ascii")}}


class _Tally:
    def __init__(self):
        self.calls = self.ptok = self.otok = 0
        self.cost = 0.0

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


# ------------------------------------------------- stage 1: read the panels
DESCRIBE_SYSTEM = """\
You are reading pages of a manhwa (a Korean comic, read top to bottom) that have \
already been cut into individual panels. For each panel image you are given, \
produce the three things an automated recap pipeline needs.

1. ocr — every piece of text visible in the panel, transcribed exactly: \
dialogue, narration boxes, sound effects, signs. Join separate text blocks with \
" / " in reading order. If the panel has no text, return an empty string. Do \
NOT transcribe scanlation watermarks, site names, Discord links or translator \
credits as if they were story text — instead, set is_credits to true and put \
what you saw in ocr so a human can confirm.

2. description — what is actually happening in the panel, in one or two \
sentences, written so someone who cannot see the image could tell it apart from \
every other panel in the chapter. Name who is present, what they are doing, and \
where. Concrete beats a summary: "a silver-haired man in black armour raises a \
greatsword over a kneeling opponent" is useful; "a dramatic fight scene" is not.

3. crop — the region worth putting on screen, as fractions of the panel \
(x0, y0, x1, y1, each 0.0-1.0, x0<x1, y0<y1). Manhwa panels are often very tall \
with the subject in one part and empty space elsewhere. Choose the tightest box \
that still contains the subject and any text that must be readable. Use \
0,0,1,1 when the whole panel is the subject. Never crop text out of a panel \
whose text carries the story.

Also set:
- is_credits: true for a scanlation credits/watermark page.
- subject: "character", "environment", "text", "action", or "effect".
- importance: 1-5, how much this panel matters to the chapter's story.

Answer for EVERY panel you are given, in order, using the panel numbers given."""

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
                    "description": {"type": "string"},
                    "crop": {"type": "array", "items": {"type": "number"}},
                    "is_credits": {"type": "boolean"},
                    "subject": {"type": "string"},
                    "importance": {"type": "integer"},
                },
                "required": ["n", "ocr", "description", "crop", "is_credits",
                             "subject", "importance"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["panels"],
    "additionalProperties": False,
}


def _clean_crop(box):
    """A crop that is inverted, out of range or vanishingly small is worse than
    no crop at all — it would silently render a sliver. Fall back to the full
    panel rather than trusting a malformed box."""
    try:
        x0, y0, x1, y1 = [float(v) for v in box]
    except (TypeError, ValueError):
        return [0.0, 0.0, 1.0, 1.0]
    x0, y0 = max(0.0, min(1.0, x0)), max(0.0, min(1.0, y0))
    x1, y1 = max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1))
    if x1 - x0 < 0.05 or y1 - y0 < 0.05:
        return [0.0, 0.0, 1.0, 1.0]
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def describe(pdir, panels, model=MODEL, progress=None, on_batch=None,
             skip=frozenset()):
    """Claude replaces OCR + description + crop choice, several panels a call.

    CHECKPOINTED AFTER EVERY BATCH. A 123-panel chapter is 21 vision calls, and
    the first version only wrote its results once all 21 had finished — so a
    container restart mid-run (a deploy, an env change) threw away everything
    already paid for. `on_batch` persists what is done so far, and `skip` lets a
    re-run resume instead of starting over.
    """
    client = validator._client()
    tally = _Tally()
    out = []
    todo = [p for p in panels if p["panel_id"] not in skip]
    if progress and skip:
        progress(f"Resuming — {len(skip)} panels already read, "
                 f"{len(todo)} to go")
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
                        "text": "Describe each panel above, by its number."})
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
                continue          # a panel number we did not send is noise
            out.append({
                "panel_id": rec["panel_id"],
                "file": rec.get("file"),
                "width": rec.get("width"), "height": rec.get("height"),
                "n": rec["_n"],
                "ocr_text": (item.get("ocr") or "").strip(),
                "visual_description": (item.get("description") or "").strip(),
                "crop_bbox_norm": _clean_crop(item.get("crop")),
                "is_credits": bool(item.get("is_credits")),
                "subject": item.get("subject", ""),
                "importance": int(item.get("importance") or 3),
                "source": "claude", "ok": True,
            })
        if on_batch:
            on_batch(out)          # survive a restart mid-chapter

    out.sort(key=lambda r: r["n"])
    return out, tally.stats(batches=len(batches), panels=len(out),
                            resumed=len(skip))


# ------------------------------------------------- stage 2: write the script
SCRIPT_SYSTEM = """\
You are writing the narration for a recap video of one manhwa chapter.

You are given every panel of the chapter in reading order, with what each one \
shows and what text appears in it. Write the narration that will be read aloud \
over those panels.

Rules:
- Tell the chapter's story in order. Past tense, third person, the voice of a \
recap narrator.
- Break it into NUMBERED UNITS. One unit is one or two sentences — a single \
beat of story that will play over one panel or a short run of panels.
- Cover the whole chapter. Do not skip a development because it was quiet.
- Skip credits pages, chapter title cards and pure sound-effect panels: they \
carry no story and should get no narration.
- Do not narrate what is only visible, unless it matters. "He drew his sword" \
if the draw matters; not "the panel shows a close-up".
- Never invent events, names or outcomes the panels do not support. If the \
panels are ambiguous, write the narration so it stays true to what is shown.
- Do not address the viewer, do not editorialise, and do not tease ("what \
happens next will shock you").

Return the units in story order, each with the text to be spoken."""

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit": {"type": "integer"},
                    "text": {"type": "string"},
                    "covers_panels": {"type": "array",
                                      "items": {"type": "integer"}},
                },
                "required": ["unit", "text", "covers_panels"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["units"],
    "additionalProperties": False,
}


def _panel_digest(d, limit=260):
    return {"panel": d["n"],
            "shows": (d.get("visual_description") or "")[:limit],
            "text": (d.get("ocr_text") or "")[:160],
            "credits": bool(d.get("is_credits"))}


def script(pdir, descs, model=MODEL, progress=None):
    """Claude replaces narrate.py. Written from the panels in one pass per
    chunk so the narration is continuous rather than per-panel captions."""
    client = validator._client()
    tally = _Tally()
    if progress:
        progress(f"Claude writing the script ({len(descs)} panels)")

    units = []
    chunks = [descs[i:i + SCRIPT_CHUNK]
              for i in range(0, len(descs), SCRIPT_CHUNK)]
    for ci, chunk in enumerate(chunks, start=1):
        if progress and len(chunks) > 1:
            progress(f"Claude writing the script ({ci}/{len(chunks)})")
        tail = ""
        if units:
            tail = ("\n\nThe narration so far ends with: "
                    + units[-1]["text"][-400:]
                    + "\nContinue from there without repeating it.")
        resp, meter, cost = validator._call_claude(
            client, model=model, max_tokens=16000,
            system=[{"type": "text", "text": SCRIPT_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT,
                           "format": {"type": "json_schema",
                                      "schema": SCRIPT_SCHEMA}},
            messages=[{"role": "user", "content":
                       "Panels in reading order:\n\n"
                       + json.dumps([_panel_digest(d) for d in chunk],
                                    ensure_ascii=False, indent=1) + tail}])
        tally.add(meter, cost)
        for u in validator._parse_json_reply(resp).get("units", []):
            txt = (u.get("text") or "").strip()
            if not txt:
                continue
            units.append({
                "scene_id": len(units),          # renumbered densely below
                "text": txt,
                "suggested_panels": [p for p in (u.get("covers_panels") or [])
                                     if isinstance(p, int)]})
    # Renumber densely so scene_id is always 0..n-1 in story order.
    for i, u in enumerate(units):
        u["scene_id"] = i
    return units, tally.stats(units=len(units), chunks=len(chunks))


# --------------------------------------------- stage 3: place & sequence
PLACE_SYSTEM = """\
You are deciding which panel each line of recap narration plays over.

You are given the narration units in order, and the chapter's panels in reading \
order with what each shows. Assign panels to units.

Rules:
- Every unit must get at least one panel. A line with no panel is heard over \
someone else's picture.
- Panels stay in READING ORDER across the whole chapter. A later unit must not \
be given a panel that comes before a panel already used by an earlier unit. The \
recap must never run backwards.
- Choose the panel that SHOWS what the line is about, not one that is merely on \
the same topic. This is the single most important judgement here.
- A unit may hold several consecutive panels when the beat plays out across \
them. A panel may be left unused — chapters contain panels no line needs.
- Never assign a credits page, a title card or a pure sound-effect panel.
- If a reveal is described in a unit, its panel must not appear before the unit \
that sets the reveal up.

Return, for each unit, the panels it plays over, in order."""

PLACE_SCHEMA = {
    "type": "object",
    "properties": {
        "placements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit": {"type": "integer"},
                    "panels": {"type": "array", "items": {"type": "integer"}},
                    "reason": {"type": "string"},
                },
                "required": ["unit", "panels", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["placements"],
    "additionalProperties": False,
}


def place(pdir, descs, units, model=MODEL, progress=None):
    """Claude replaces matcher.py — line-to-panel fit AND story sequencing."""
    client = validator._client()
    tally = _Tally()
    if progress:
        progress(f"Claude placing {len(units)} lines onto {len(descs)} panels")

    by_n = {d["n"]: d for d in descs}
    resp, meter, cost = validator._call_claude(
        client, model=model, max_tokens=32000,
        system=[{"type": "text", "text": PLACE_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT,
                       "format": {"type": "json_schema",
                                  "schema": PLACE_SCHEMA}},
        messages=[{"role": "user", "content":
                   "NARRATION UNITS:\n"
                   + json.dumps([{"unit": u["scene_id"], "text": u["text"]}
                                 for u in units], ensure_ascii=False, indent=1)
                   + "\n\nPANELS IN READING ORDER:\n"
                   + json.dumps([_panel_digest(d) for d in descs],
                                ensure_ascii=False, indent=1)}])
    tally.add(meter, cost)

    placements = {}
    used = set()
    for p in validator._parse_json_reply(resp).get("placements", []):
        u = p.get("unit")
        panels = [n for n in (p.get("panels") or [])
                  if isinstance(n, int) and n in by_n and n not in used]
        for n in panels:
            used.add(n)
        if panels:
            placements[u] = {"panels": panels, "reason": p.get("reason", "")}

    # Build the script.json shape the board and validator already understand,
    # so the experiment's output can be measured by the SAME checker as the
    # baseline rather than by a bespoke one.
    scenes = []
    for u in units:
        got = placements.get(u["scene_id"], {})
        scenes.append({
            "scene_id": u["scene_id"],
            "text": u["text"],
            "panel_ids": [by_n[n]["panel_id"] for n in got.get("panels", [])],
            "panel_numbers": got.get("panels", []),
            "reason": got.get("reason", ""),
        })
    return scenes, tally.stats(
        units=len(units), placed=sum(1 for s in scenes if s["panel_ids"]),
        panels_used=len(used))


# ------------------------------------------------ stage 4: timing & motion
TIMING_SYSTEM = """\
You are planning how each panel of a recap video is shown: how long it stays on \
screen and how the camera moves over it.

IMPORTANT: there is no audio in this experiment, so you are PROPOSING timings, \
not fitting them to a real voice track. Assume a narrator reading at about 2.6 \
words per second, and give each unit enough time for its line to be spoken.

For each panel give:
- seconds: time on screen. Split a unit's total across its panels — a panel \
carrying the key image of a beat deserves more than a cutaway.
- motion: one of "static", "push-in", "pull-out", "pan-up", "pan-down", \
"pan-left", "pan-right". Tall panels usually want a vertical pan; a face or a \
reveal usually wants a push-in; a wide establishing shot usually wants static \
or a slow pull-out.
- transition: "cut" or "fade".

Rules:
- No panel under 1.0 seconds; below that it registers as a flicker, not an image.
- No panel over 12 seconds unless the line over it genuinely runs that long.
- Vary the rhythm: a chapter cut entirely to one duration reads as a slideshow."""

TIMING_SCHEMA = {
    "type": "object",
    "properties": {
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "panel": {"type": "integer"},
                    "seconds": {"type": "number"},
                    "motion": {"type": "string"},
                    "transition": {"type": "string"},
                },
                "required": ["panel", "seconds", "motion", "transition"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["panels"],
    "additionalProperties": False,
}


def timing(pdir, descs, scenes, model=MODEL, progress=None):
    """Claude proposes the on-screen timing & motion column. PROPOSED, not
    measured — there is no audio in this experiment."""
    client = validator._client()
    tally = _Tally()
    if progress:
        progress("Claude planning timing and motion")

    by_n = {d["n"]: d for d in descs}
    items = []
    for sc in scenes:
        for n in sc.get("panel_numbers", []):
            d = by_n.get(n)
            if not d:
                continue
            items.append({"panel": n, "unit": sc["scene_id"],
                          "words": len((sc.get("text") or "").split()),
                          "shows": (d.get("visual_description") or "")[:180],
                          "aspect": round((d.get("height") or 1) /
                                          max(1, d.get("width") or 1), 2)})
    if not items:
        return [], tally.stats(planned=0)

    resp, meter, cost = validator._call_claude(
        client, model=model, max_tokens=16000,
        system=[{"type": "text", "text": TIMING_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT,
                       "format": {"type": "json_schema",
                                  "schema": TIMING_SCHEMA}},
        messages=[{"role": "user", "content":
                   "Plan the timing for these panels.\n\n"
                   + json.dumps(items, ensure_ascii=False, indent=1)}])
    tally.add(meter, cost)

    out = []
    for p in validator._parse_json_reply(resp).get("panels", []):
        d = by_n.get(p.get("panel"))
        if not d:
            continue
        out.append({"panel_id": d["panel_id"], "n": d["n"],
                    "dur": max(0.1, round(float(p.get("seconds") or 0), 2)),
                    "motion": p.get("motion", "static"),
                    "transition": p.get("transition", "cut")})
    out.sort(key=lambda r: r["n"])
    return out, tally.stats(planned=len(out))


# ------------------------------------------------------------------ driver
def manifest_path(pdir):
    return os.path.join(out_dir(pdir), "manifest.json")


def load_manifest(pdir):
    return read(pdir, "manifest.json")


def run(pdir, stages=STAGES, model=None, progress=None):
    """Run the Claude variant over an ALREADY-INGESTED chapter.

    The chapter must already have crops, because scraping and splitting are
    shared infrastructure rather than Gemini judgements — running both variants
    off the SAME crops is what makes the comparison fair.

    TWO KINDS OF FAILURE, handled differently on purpose:
      - PRE-FLIGHT refusals (not ingested, past the panel cap) RAISE. Nothing
        has been spent yet, so the caller should be stopped cleanly rather than
        handed an empty manifest that looks like a run.
      - MID-RUN failures (a refusal, a cap breach, an unusable reply) return an
        error manifest. Work has already been done and paid for by then, and
        throwing it away would mean paying for it twice.
    """
    model = model or MODEL
    started = time.time()
    panels = baseline_panels(pdir)
    if not panels:
        raise PipelineError(
            "This project has no descriptions.json, so it has not been "
            "ingested yet. Ingest the chapter normally first — that produces "
            "the panel crops and the Gemini baseline this experiment is "
            "measured against.")
    if len(panels) > MAX_PANELS:
        raise PipelineError(
            f"{len(panels)} panels is past the {MAX_PANELS}-panel experiment "
            "cap. Raise CLAUDE_TEST_MAX_PANELS deliberately if that is really "
            "what you want to spend.")
    for i, rec in enumerate(panels, start=1):
        rec["_n"] = i

    man = load_manifest(pdir) or {}
    man.update({
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model, "effort": EFFORT,
        "panels": len(panels),
        "stages_run": list(stages),
        "status": "ok", "error": None,
        "timing_is_proposed": True,
        "audio": "untouched — this experiment does not generate or alter audio",
        "cost_usd": man.get("cost_usd", 0.0),
        "calls": man.get("calls", 0),
        "passes": man.get("passes", {}),
    })

    def _absorb(name, st):
        man["passes"][name] = st
        man["cost_usd"] = round(man["cost_usd"] + st["cost_usd"], 6)
        man["calls"] += st["calls"]

    try:
        if "describe" in stages:
            # Anything a previous (possibly interrupted) run already read is
            # kept and not paid for twice.
            prior = read(pdir, "descriptions.json", []) or []
            prior = [d for d in prior if d.get("visual_description")]
            done_ids = {d["panel_id"] for d in prior}

            def _checkpoint(partial):
                merged = prior + [d for d in partial
                                  if d["panel_id"] not in done_ids]
                merged.sort(key=lambda r: r.get("n", 0))
                _write(pdir, "descriptions.json", merged)

            fresh, st = describe(pdir, panels, model=model, progress=progress,
                                 on_batch=_checkpoint, skip=done_ids)
            _checkpoint(fresh)
            if not read(pdir, "descriptions.json"):
                raise PipelineError("Claude returned no panel readings.")
            _absorb("describe", st)
        descs = read(pdir, "descriptions.json", [])
        if not descs:
            raise PipelineError("No Claude descriptions yet — run that stage first.")

        if "script" in stages:
            units, st = script(pdir, descs, model=model, progress=progress)
            if not units:
                raise PipelineError("Claude wrote no narration units.")
            _write(pdir, "units.json", units)
            _absorb("script", st)
        units = read(pdir, "units.json", [])

        if "place" in stages:
            scenes, st = place(pdir, descs, units, model=model,
                               progress=progress)
            _write(pdir, "script.json", scenes)
            _absorb("place", st)
        scenes = read(pdir, "script.json", [])

        if "timing" in stages:
            plan, st = timing(pdir, descs, scenes, model=model,
                              progress=progress)
            _write(pdir, "timing.json", plan)
            _absorb("timing", st)
    except (validator.ValidatorError, usage.UsageCapExceeded,
            PipelineError) as e:
        man["status"] = "error"
        man["error"] = str(e)
        man["elapsed_sec"] = round(time.time() - started, 1)
        _write(pdir, "manifest.json", man)
        return man

    man["elapsed_sec"] = round(time.time() - started, 1)
    _write(pdir, "manifest.json", man)
    return man


# ---------------------------------------------------------------- promote
# Turning the experiment into something you can actually WATCH.
#
# The comparison above answers "did Claude decide better?". It cannot answer
# "is the result any good to watch", because a sidecar has no audio and no
# render segments. So promote() builds a REAL, SEPARATE project out of the
# Claude variant — one the board, approve, export and Review pages already
# understand, because it is the same shape every other project is.
#
# WHAT IS REUSED UNCHANGED (the owner's constraint: do not replace the audio
# path): beat segmentation, the existing REST TTS with its hash-keyed cache,
# and build_segments. The narration AUDIO is produced by exactly the code that
# produces it for every other chapter.
#
# WHAT IS CLAUDE'S INSTEAD OF GEMINI'S: the panel readings, the descriptions,
# the crop boxes, the script text, and which panel each line plays over. The
# Gemini matcher and the Gemini shot planner are NOT run — Claude already made
# those calls, and re-running them would overwrite the very decisions the
# experiment exists to evaluate.
#
# The baseline project is never opened for writing. The promoted project is a
# sibling directory; deleting it removes the experiment entirely.

def promoted_id(pdir):
    return os.path.basename(pdir.rstrip("/")) + "-claude"


def promote(pdir, progress=None, tts=True):
    """Build a playable sibling project from the Claude variant."""
    import shutil
    HERE_ = os.path.dirname(os.path.abspath(__file__))
    RECAP = os.path.dirname(HERE_)
    for p in (RECAP, os.path.join(RECAP, "hyperframes")):
        if p not in os.sys.path:
            os.sys.path.insert(0, p)
    import beat_segmenter
    from segments import build_segments

    descs = read(pdir, "descriptions.json", []) or []
    scenes = read(pdir, "script.json", []) or []
    if not descs or not scenes:
        raise PipelineError(
            "Run the Claude pipeline first — there is no experiment output to "
            "promote yet.")

    dest = os.path.join(os.path.dirname(pdir.rstrip("/")), promoted_id(pdir))
    os.makedirs(dest, exist_ok=True)
    audio_dir = os.path.join(dest, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(os.path.join(dest, "clips"), exist_ok=True)

    # The SAME crops as the baseline — that is what keeps the comparison fair
    # and what stops a second copy of a chapter's art existing on disk.
    src_crops = os.path.join(pdir, "crops")
    dst_crops = os.path.join(dest, "crops")
    if not os.path.exists(dst_crops):
        try:
            os.symlink(src_crops, dst_crops)
        except (OSError, NotImplementedError):
            shutil.copytree(src_crops, dst_crops)

    by_pid = {d["panel_id"]: d for d in descs}

    # ---- beats: the shared segmenter, not a Claude-specific one -------
    if progress:
        progress("Segmenting Claude's script into beats")
    beats = beat_segmenter.segment_beats_scenes(
        [{"scene_id": s["scene_id"], "text": s["text"],
          "panel_ids": s.get("panel_ids", [])} for s in scenes])
    if not beats:
        raise PipelineError("Claude's script produced no beats.")

    # ---- audio: the EXISTING TTS path, untouched ----------------------
    t = 0.0
    if tts:
        import server as srv
        for i, b in enumerate(beats):
            out = os.path.join(audio_dir, f"beat_{b['index']:03d}.mp3")
            if not os.path.exists(out):
                srv._synth_rest(b["text"], out)
            d = _audio_len(out)
            b["start"], b["end"] = round(t, 3), round(t + d, 3)
            nxt = beats[i + 1] if i + 1 < len(beats) else None
            # Same scene-aware rhythm the production pipeline uses.
            if nxt is not None and "scene_id" in b and "scene_id" in nxt:
                t += d + (0.6 if nxt["scene_id"] != b["scene_id"] else 0.25)
            else:
                t += d + 0.35
            if progress and i % 10 == 0:
                progress(f"Voicing beat {i + 1}/{len(beats)} · {t:.0f}s")
    else:
        # No-TTS mode exists for tests only: it fabricates a plausible
        # timeline so the segment shapes can be exercised without spending
        # TTS characters. Never used by the route.
        for b in beats:
            d = max(1.2, len(b["text"].split()) / 2.6)
            b["start"], b["end"] = round(t, 3), round(t + d, 3)
            t += d + 0.3

    # ---- shots: Claude's placement, folded like the production board ---
    if progress:
        progress("Laying Claude's panels onto the narration timeline")
    beats_by_scene = {}
    for b in beats:
        beats_by_scene.setdefault(b.get("scene_id"), []).append(b)

    shots = []
    for sc in scenes:
        sb = sorted(beats_by_scene.get(sc["scene_id"], []),
                    key=lambda x: x["start"])
        pids = [p for p in (sc.get("panel_ids") or []) if p in by_pid]
        if not sb or not pids:
            continue
        win0, win1 = sb[0]["start"], sb[-1]["end"]
        span = max(0.001, win1 - win0)
        step = span / len(pids)
        for i, pid in enumerate(pids):
            s0 = win0 + i * step
            s1 = win1 if i == len(pids) - 1 else s0 + step
            mid = (s0 + s1) / 2.0
            # The beat actually audible over this slice — that is the text the
            # board shows on the row, and folded panels legitimately share one.
            b = next((x for x in sb if x["start"] <= mid <= x["end"]), None) or \
                min(sb, key=lambda x: abs((x["start"] + x["end"]) / 2 - mid))
            d = by_pid[pid]
            shots.append({
                "index": b["index"], "start": round(s0, 3), "end": round(s1, 3),
                "beat_text": b["text"],
                "panel_id": pid, "panel_file": os.path.join(dst_crops,
                                                            d.get("file") or f"{pid}.png"),
                "width": d.get("width"), "height": d.get("height"),
                "crop_bbox_norm": d.get("crop_bbox_norm") or [0.0, 0.0, 1.0, 1.0],
                "focus_source": "claude",
                "focus_reason": "crop chosen by Claude from the panel art",
                "focus_confidence": 1.0,
            })
    if not shots:
        raise PipelineError(
            "Claude placed no line on any panel, so there is nothing to render.")
    shots.sort(key=lambda s: s["start"])

    # Exact tiling, the same way matcher.build_timeline does it: snap every
    # shot's end to the next shot's start. Without this the pauses the TTS
    # rhythm inserts BETWEEN scenes are left uncovered, and the render shows
    # black for a beat at every scene change.
    for i in range(len(shots) - 1):
        shots[i]["end"] = shots[i + 1]["start"]
    for sh in shots:
        sh["dur"] = round(sh["end"] - sh["start"], 3)

    segs = build_segments(shots)
    with open(os.path.join(dest, "segments.json"), "w", encoding="utf-8") as f:
        json.dump(segs, f, indent=2)

    # ---- the files the board reads -----------------------------------
    with open(os.path.join(dest, "descriptions.json"), "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in d.items() if k != "n"} for d in descs],
                  f, indent=2)
    with open(os.path.join(dest, "script.json"), "w", encoding="utf-8") as f:
        json.dump([{"scene_id": s["scene_id"], "text": s["text"],
                    "panel_ids": s.get("panel_ids", [])} for s in scenes],
                  f, indent=2)
    with open(os.path.join(dest, "script.txt"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(s["text"] for s in scenes))

    base_meta = {}
    try:
        with open(os.path.join(pdir, "project.json"), encoding="utf-8") as f:
            base_meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    meta = {
        "id": promoted_id(pdir),
        "url": base_meta.get("url", ""),
        "crops": dst_crops, "audio": audio_dir,
        "descriptions": os.path.join(dest, "descriptions.json"),
        "n_segments": len(segs),
        "duration": round(shots[-1]["end"], 1),
        "series": base_meta.get("series", ""),
        "chapter": base_meta.get("chapter", ""),
        "part": base_meta.get("part", ""),
        # Stamped so this can never be mistaken for a normal chapter, on the
        # board, in the project list or six months from now.
        "match_method": "claude-experiment",
        "experiment": True,
        "experiment_of": os.path.basename(pdir.rstrip("/")),
        "experiment_note": "Panels, descriptions, crops, script and placement "
                           "by Claude. Audio by the standard TTS path.",
    }
    with open(os.path.join(dest, "project.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    man = load_manifest(pdir) or {}
    man["promoted_to"] = meta["id"]
    man["promoted_segments"] = len(segs)
    man["promoted_duration"] = meta["duration"]
    _write(pdir, "manifest.json", man)
    return {"project": meta["id"], "dir": dest, "segments": len(segs),
            "beats": len(beats), "duration": meta["duration"]}


def _audio_len(path):
    import subprocess
    try:
        return round(float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], text=True).strip()), 3)
    except Exception:
        return 2.5
