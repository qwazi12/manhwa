# Pipeline Handbook — the default (Gemini) system vs the Claude lab

Every decision point, threshold and prompt rule in both pipelines, read out of
the code rather than remembered. Written to answer one question: where is the
Claude lab under-specified compared to the system that has been tuned for
months, and what is worth porting across.

Source files: `scraper.py`, `panel-split/split_panels.py`,
`panel-split/vision_segment.py`, `panel-describe/describe.py`, `narrate.py`,
`beat_segmenter.py`, `matcher.py`, `shot_planner.py`,
`hyperframes/segments.py`, `review_ui/claude_lab.py`,
`review_ui/claude_pipeline.py`; for running it (Part E): `review_ui/autopilot.py`,
`review_ui/project_archive.py`, `review_ui/usage.py`, `gemini_tts.py`.

> **Updated 2026-10-03.** Describe and narrate moved to **Gemini 3.8 Flash**
> (`PIPELINE_MODEL`); autopilot chapters run on Google's **Flex** tier; spend is
> now metered from real token counts at published prices; the voice default is
> Gemini 3.8 Flash TTS. New **Part E** covers Chapter Autopilot, the review
> inbox, stop/resume, archiving, and cost.

---

## 0. The shared spine

Both pipelines are the same seven stages. What differs is who decides and how
tightly they are constrained.

```
download → cut into panels → read each panel → write script
         → segment into beats → voice → match/place → crop → build segments
```

Two stages are genuinely shared, byte for byte:

- **Download** (`scraper.py`). `find_page_urls` parses the chapter HTML;
  `MIN_PLAUSIBLE_PAGES = 5`; `_sequence_warning` detects gaps in the image
  numbering and sets `LAST_WARNING`, which the UI surfaces — added because a
  chapter that scraped 3 of 11 pages looked completely successful.
- **Voice** (`gemini_tts.py`). Default **Gemini 3.8 Flash TTS**, voice
  *Charon*, style "dramatic, engaging manhwa recap narrator"; metered per call
  ($0.50 / 1M text in, $9.00 / 1M audio out at 25 audio tokens per second; both
  double on 2027-01-01). Chirp 3 HD (`server._synth_rest`, REST + API key) is
  still one pick away in the voice picker, and a chapter keeps the voice it was
  first voiced with. Results cached by `hash(voice + text)` in
  `projects/_ttscache`, so an unchanged sentence re-synthesises for free. Timeline rhythm is scene-aware:
  **0.6s** pause at a scene boundary, **0.25s** within a scene, **0.35s** flat
  when there is no provenance.

---

# PART A — the default (Gemini) system

## A1. Cut into panels — `splitlab.split_into` (since 2026-09-23)

> **This section was rewritten on 2026-09-24.** It previously described
> `panel-split/split_panels.py` as the production splitter. That changed:
> background registration is now the default ingest path, and the four-pass
> detector below is the FALLBACK. Restore the old behaviour with
> `SPLIT_ENGINE=legacy`.

### The rule

**A panel is what lies between two breaks. A break is a band of rows that is
background across the full width.** Nothing else fires a cut — which is why
speech bubbles need no special handling: a bubble never spans edge to edge, so
a row crossing one always contains non-background pixels and can never qualify.

### Registering the background — per page, never global

The gutter colour is the **mode of the row-mean distribution**. In a chapter
the gutter is the single most-repeated full-width row type, so the modal row
IS the gutter — on dark pages, light pages, and bleed pages where art touches
the edges (which is why this samples row means rather than the page border).

Tolerance comes from the spread of that modal cluster itself, floored at 14
grey levels. Not a constant, and not the spread of the whole page.

The old `_estimate_background_color` voted near-white against near-black and
returned 255 or 0. Measured on Fated Villain ch.353 it returned 0 for all ten
pages while the true modal row was 64.7, 38.8 and 105.6 on three of them — and
on the worst, **0 of 800 rows** then qualified as gutter, so the page could not
be split geometrically at all.

### Strip format

A WEBTOON CDN tile is **not a page** — it is an arbitrary ~1280px slice of one
continuous scroll, and it can land mid-panel. Tiles are concatenated, the
scroll is registered **once**, and panels are stitched back across tile seams.
Per-tile registration produced **376 sliver fragments** with backgrounds
varying 16.9–254; one-scroll registration gives **93 panels, median AR 2.34**.

Format is decided by **tile-height uniformity**, not by site name: a CDN
slicing one scroll emits equal-height tiles (137 of 138 identical), while real
pages vary (504 … 16000 on one chapter). An aspect-ratio test read exactly
backwards here — Asura serves very tall pages, WEBTOON modest tiles.

### Flat bands — off by default

A gutter is a flat row *whatever its colour*, so a white band on a black page
is a real break the single-background test cannot see. This is **off globally**
and enabled per title (`FLAT_STD_BY_TITLE`), because it is verifiably right on
Fated Villain (4 gutters, each checked, including a 157-row band at mean 87.6)
and over-fires on Murim by +19%.

A recurrence rule was tested as a way to keep both — "gutters repeat, one-off
art fills do not" — and **rejected by measurement**: it deleted 2 of the 4
verified gutters, because a real gutter can be unique on its page when it
divides panels of differing ground colour.

### Evidence for the swap

Cut-by-cut arbitration on the pinned Murim ch.43 fixture. Of **161** cuts the
two splitters disagree on:

| | count |
|---|---|
| interior features the legacy splitter was shredding | **143** |
| real gutters this splitter misses | **18** |

Better on net, **not strictly better** — the 18 are a recorded cost.
Confirmed in production on ch.44: 21 pages → **154 panels**, matching the
preview measurement exactly.

### The fallback — the four-pass detector

`panel-split/split_panels.py` still ships and runs whenever the new path
raises, so a chapter that has already paid to scrape never dies at the split
stage. Coverage-gated, four passes, no pass trusted alone:

1. **YOLO** (`conf=0.3`), each box through `_apply_bleed_guard` (expands up to
   30px to avoid clipping art), ≥ `MIN_PANEL_SIZE = 80` px on both axes.
2. **Geometric gutter split** over content bands YOLO left uncovered.
3. **Anchors force-include what both missed** — speech bubbles
   (`BUBBLE_MIN_AREA = 4000` px², `BUBBLE_MAX_AREA_FRAC = 0.25`) and figures.
   An anchor is safe if ≥ `ANCHOR_COVERED_FRAC = 0.6` of it lies inside a kept
   box.
4. **Fail-open**: remaining content bands ship as density crops.

## A2. Read each panel — `describe.py`

One `VISION_PROMPT`, JSON only, exactly two keys.

**OCR rules:** all readable text in bubbles, captions and sound effects, joined
with `' / '`. *"Transcribe EXACTLY as written — names, curses, sound effects all
matter for matching."* Empty string if none. Punctuation-only bubbles such as
`!!!` or `ACK!!` are kept as-is. The prompt states outright: **"OCR text is the
highest-priority match signal."**

**Description rules — the tightly-specified part:**

- **MAX 50 words**, stated twice.
- **MUST** open with the concrete action-verb phrase, *not* the subject, with a
  worked contrast example showing the right and wrong form.
- Four worked examples of good action phrases.
- **Banned openers:** "a scene showing", "a panel depicting", "the image
  features".
- Then: who is in frame (name if visible), setting, shot type.
- "Describe only what is visibly in THIS panel. Do not invent plot."

**Plumbing:** two API paths (Interactions API for `AQ.` keys, legacy
`generateContent` otherwise); Tesseract OCR-only fallback when no key;
`--merge` mode re-describes only panels whose dimensions changed; usage-gated;
a cap breach halts the whole run rather than being swallowed per panel.

**Model and tier (since 2026-10-03):** `PIPELINE_MODEL`, default
`gemini-3.8-flash` (was 3.5 Flash; rollback = set the env var). Runs as a
subprocess, so an autopilot chapter's tier arrives as `RECAP_SERVICE_TIER=flex`
and is sent as `"service_tier": "flex"`, with a 900 s timeout because Flex can
queue. A 429/503 on Flex is retried once on Standard: Google has no server-side
fallback. Token counts come from `usage.parse_gemini_usage` (see E5).

## A3. Write the script — `narrate.py`

The most constrained stage in the system, and a **two-pass** one.

Model: `narrate.DEFAULT_MODEL` = `PIPELINE_MODEL` (default `gemini-3.8-flash`),
the same setting as describe. Same Flex behaviour for autopilot chapters,
requested through `usage.set_tier()` on the ingest thread (narrate runs
in-process, single-threaded).

### Pass 1 — draft, per scene

**Computed word budget** — the structural density control:

```
word_budget(n_panels, n_dialogue) = max(40, min(220, 12*n_panels + 7*n_dialogue))
```

Delivered as: *"STRICT LENGTH BUDGET: write AT MOST N words for this ENTIRE
scene — count them. Compress ruthlessly."* Dialogue-heavy pages **earn** more
narration, because a flat panel-only budget once compressed an 8-bubble page
into one sentence.

**STYLE CONTRACT — nine mandatory numbered rules:**

1. Third-person, past tense, story-first — one flowing narrative.
2. Every sentence carries an EVENT, REACTION, REALIZATION, INTENTION or
   CONSEQUENCE. A sentence that only describes how something looks is cut.
2b. **Dialogue fidelity** — every meaningful exchange gets its own
   reported-speech sentence. Collapsing a conversation into one summary line is
   a contract violation. Only trivial fillers may fold.
3. All visible dialogue converted to reported narration — **never quotation
   marks**.
4. **No panel/framing/camera/art language, ever** — never "the panel shows",
   "close-up", "speed lines", "we see", and never the word "camera".
4b. Each scene continues where the previous left off; never re-tell.
5. Appearance, clothing and setting only when plot-relevant — one economical
   touch, not an inventory.
6. **Enrichment policy** — infer motive, emotion and subtext when clearly
   implied; never invent names, numbers, backstory or events without support.
7. **"DENSITY IS EDITORIAL, NOT MECHANICAL."** A run of panels showing one
   continuous action gets ONE sentence. A filler panel earns ZERO. Only a true
   story peak earns 2–3. Never average sentences per panel.

Plus a **VOICE ANCHOR** of three worked cadence examples, a **STYLE_ANCHOR**
reference prose sample, and a **global beatsheet** — a running chapter summary
so each scene knows where in the story it is.

### Pass 2 — critique and revise (`critique_units` → `revise_unit`)

One reviewer call over the **whole** draft, returning a JSON issue list typed as
`hallucination | misorder | missed_beat | style_violation | redundancy`:

- **hallucination** — names, numbers, events or motives with no support in the
  panel facts
- **misorder** — events narrated in a different order than the panels
- **missed_beat** — a clearly major event the draft skips
- **style_violation** — quoted dialogue, present tense, or any mention of
  camera/panel/image/frame
- **redundancy** — re-telling what an earlier unit already narrated

Each flagged unit is then regenerated with the editor's notes appended to the
original prompt, preserving provenance by construction.

**Output:** `script.txt` plus `script.json` = `[{scene_id, panel_ids, text}]`.

## A4. Segment into beats — `beat_segmenter.py`

One beat = one full sentence, so TTS voices a complete thought and the matcher
aligns a whole semantic unit.

- `MIN_BEAT_WORDS = 4` — shorter sentences merge into the previous beat.
- `MAX_BEAT_WORDS = 45` — longer ones clause-split on `;`, ` — `, or
  `, and/but/so`.
- Abbreviations protected: mr, mrs, ms, dr, st, vs, etc, e.g, i.e.
- `segment_beats_scenes` re-indexes globally and stamps every beat with its
  `scene_id` and `panel_ids` — this is the **provenance** the matcher is
  constrained by.

## A5. Match beats to panels — `matcher.py`

**Global dynamic programming, forward-only.** Not greedy: a greedy matcher makes
each choice locally and can never revisit it, so errors compound and the video
drifts out of sync — one panel once swallowed 10+ beats / 46s.

**State:** `dp[j][r]` = best total score with the current beat on panel `j`,
having been there `r+1` consecutive beats.

**Transitions:**
- hold → same panel, cost `−HOLD_PENALTY = 0.06`
- over-hold → beyond `MAX_HOLD = 5`, cost `−OVER_HOLD_PENALTY = 0.5` per beat
  (steep but **finite**, so the finale can keep holding the last real panel
  rather than advancing onto a blank title card)
- advance → any later panel, free

**Scoring:** Gemini embeddings (`gemini-embedding-2`, task
`SEMANTIC_SIMILARITY`; $0.20 / 1M tokens; the response carries no token count,
so each batch is metered with the free `count_tokens` call; Flex does not apply
to embeddings), disk-cached, `EMBED_BATCH = 32`, `EMBED_RETRIES = 4`,
transient-error detection, and a one-off probe that drops to per-text embedding
if the installed SDK does not honour batching. Deterministic lexical fallback
(`OCR_WEIGHT = 0.55`, `DESC_WEIGHT = 0.45`) so it runs with no external service.

**Image check first (`panel_stats.py`, since 2026-10-04 — see E8).** Each
panel carries a `role` from its own pixels: `art`, `bubble`, `fragment` or
`credits`. Bubble/fragment/credits are junk whatever the description says; an
`art` panel with ink ≥ 0.30, both sides ≥ 120 px and a description is never
dropped by a wording rule. Panels without a role (older projects) fall through
to the rules below unchanged.

**Junk filter (`is_junk_panel`) — two regimes:**

- *Vision-segmented* beats are model-curated, so the keyword filter does not
  apply. Junk only if physically empty: a degenerate crop under
  `_MIN_BEAT_PX = 40`, or no text **and** no description.
- *Geometric* crops use a **positive-keep** rule: junk unless the description
  names a real subject or a real scene, minus an abstract/decorative override
  that drops bubbles, slivers, bare SFX and blanks.
- `_PROMO_BLOCK` (promo/credits) outranks every keep-rule in every regime.

Junk gets a steep finite cost, so the path avoids it unless nothing else is
reachable.

**Provenance constraint:** a beat may only land on a panel from its own scene;
violation cost `CONSTRAINT_COST = −1e6` (steep but finite, so a malformed
provenance list can't make the DP insoluble).

**Importance bonus:** `panel_importance` = normalised area + 0.5 if ≥ 4 OCR
words + 0.3 if the description names both a subject and a scene, weighted at
`IMPORTANCE_W = 0.15`. `HOLD_CAP_S = 12.0` caps seconds on one panel.

**Timeline:** `build_timeline` emits one shot per beat, then snaps each shot's
end to the next shot's start — exact tiling, no gaps, no overlaps.

## A6. Choose the crop — `shot_planner.py`

**Conditional and narration-driven — this is the stage most unlike the lab.**

- `should_crop_close(text)` gates everything: a crop analysis runs **only** if
  the assigned narration mentions one of ~30 `_DETAIL_KEYWORDS` — face, eyes,
  glare, expression, hand, fist, grip, sword, blade, dagger, chain, wound,
  blood, bubble, caption, shout, scream, tear… Everything else stays full frame.
- When it fires, the model (`gemini-2.5-flash`) is given the panel image **and
  the list of narration beats assigned to it**, and returns per beat:
  `crop_bbox_norm`, `framing_mode` (`close_up|medium|full`), `focus_reason`,
  `focus_confidence`. Run in parallel via `ThreadPoolExecutor`.
- **One crop contract**, shared by the exporter and the editor:
  `FULL_FRAME_TOL = 0.01`, `CROP_MIN_AREA = 0.12`, and
  `normalize_crop` / `crop_status` / `effective_crop` / `is_sub_crop`. This
  exists because each side once decided for itself — the exporter treated any
  box as a crop, the editor ignored it entirely, and 41 of 82 segments rendered
  differently from what the board showed.
- `get_crop_layout` computes `scale_w/scale_h/left/top/w/h/ar` against the
  1920×1080 card constraints (max-width 46%, max-height 90%).

**The ordering matters:** the crop is chosen **after** the script exists and is
driven by it.

## A7. Build segments — `hyperframes/segments.py`

`build_segments` opens a new segment whenever `panel_id` **or**
`crop_bbox_norm` changes, collects the beats that play over it, and stamps
`dur` and `clip = clips/seg_XXX.mp4`.

---

# PART B — the Claude lab (`claude_lab.py`, `claude_plus.py`, `claude_place.py`)

> **This part was rewritten on 2026-09-19.** The previous version described
> `claude_pipeline.py`, the first draft of the lab. Its "absent compared to the
> default" lists — no coverage target, no DP, no provenance constraint — were
> accurate then and are wrong now: the Claude+ upgrade added all three. Treat
> any older copy of this section as describing a pipeline that no longer runs.

## B1. Cut into panels — graduated to production, tab archived

> **Rewritten 2026-09-24.** This section described a `claude | yolo` switch in
> the TEST tab. Both are gone as an operator-facing choice: the
> background-registration splitter became the production path (see A1) and the
> Split and TEST rail tabs were archived on 2026-09-23. Their drawers and
> routes remain so existing runs stay openable.

The lab no longer owns a splitter of its own worth documenting separately. A
Claude-driven splitter (`split_with_claude`) still exists in `claude_lab.py`
— one vision call per page at `PAGE_MAX_PX = 1400`, validated against a
`COVERAGE_TARGET = 0.85` ink gate with gap recovery, tall-panel recursion and
blank rejection — but it is not the path any ingest takes, and it costs money
that A1's splitter does not.

**What the operator chooses now is the ENGINE, not the splitter.** One ingest
entry point takes `engine = gemini | claude`, recorded per project.
**Gemini is the default**: a newer path earns default status with data, not by
being newer. Both engines use the same A1 splitter.

*Known gap, filed:* the lab listing still keys on the `-lab-` substring in a
folder name while the engine now lives in a field, so the two can drift. The
rename is deferred until `run_lab` is next touched, and must be atomic across
`lab_id`, `lab_dir` and that filter.

## B2. Read each panel — `claude_plus.describe_plus`

Batched vision calls. Each panel returns more than prose:

| Field | Why it exists |
|---|---|
| `ocr` | the words actually on the panel |
| `visual_description` | what is depicted |
| `ocr_confidence` | feeds `panel_weights()` at placement time |
| `desc_confidence` | low confidence is surfaced, not hidden |
| `needs_review` | the model flagging its own uncertainty |
| `subject_type` | `character / scene / object / text / credits` |
| `focus_hint` | **advisory only** — never a crop box |

`audit_description()` checks the prose for the failure modes that matter
(missing action verb, hedging, restating the OCR), and **exempts static, text
and credits panels** from the action-verb rule, because a title card has no
action to describe and flagging it is noise.

**Resumable.** Descriptions are checkpointed to `descriptions.json` after every
batch and the stage skips panels already read (`skip=done`), so a run killed
here resumes without re-paying. Cost is banked per batch, so a run that dies
half way still reports what it actually spent.

## B3. Write the script — `claude_plus.script_plus`

Scene-aware and **budgeted**, which is the part the default does not do:

- `word_budget(n_panels, n_dialogue)` sets how much narration a scene has
  earned from how much is actually on the page.
- `scene_budget()` applies it per scene, with a dense-scene allowance.
- The budget is a **target range**, not a point: `TARGET_MIN_FRAC = 0.50`,
  `TARGET_MAX_FRAC = 0.90`. A single number made the model pad to hit it.

Then critique and revise, with issue types including `missing_worldbuilding`.
`underspend_issues()` is computed **in code** rather than asked of the model —
under-spend is arithmetic the pipeline already has, and asking a model to check
arithmetic it just produced is how you get agreement instead of an answer.

## B4. Place lines on panels — `claude_place.place`

A **dynamic-programming solver**, not a single "assign these" call. Forward-only
(a line may not reach backwards into an earlier panel), with tuned costs:

| Cost | Value | What it buys |
|---|---|---|
| `HOLD_PENALTY` | 0.06 | mild resistance to sitting on one panel |
| `OVER_HOLD_PENALTY` | 0.5 | sharp resistance past the cap |
| `HOLD_CAP_S` | 12.0s | the point where a hold becomes a stare |
| `JUNK_COST` | 3.0 | keeps lines off credits / SFX / title cards |
| `PROVENANCE_COST` | 0.3 | a line should land on the panel it was WRITTEN from |

`PROVENANCE_COST` was calibrated by sweep, not guessed: **0.9** pinned every
line to its source panel and escapes fell to zero (a wall); **0.5** allowed one
escape; **0.3** restored full recovery while still preferring provenance.

`panel_weights()` weights candidates by OCR confidence, so a panel the reader
could not read is a weaker target. `expand_units()` then spreads a unit across
consecutive panels for visual progression, with `MIN_VISUAL_SEC = 1.4` and a
`_distinct()` check so "progression" means a genuinely different image.

## B5. Choose the crop — **after** the script, not during the read

The single most important structural change. The first draft chose crops during
the read pass, which made it *structurally impossible* for framing to serve the
narration — the line did not exist yet.

Crops are now planned last, by `claude_plus.plan_crops`, and every proposed box
is run through the shared `crop_score.choose_crop` with the **RAW** box (an
earlier bug normalised slivers before measuring them, which made rejections
unattributable). Full frame wins unless a crop beats it by a real margin *and*
passes lint. `focus_confidence` is never the gate — the two worst boxes in the
Martial Genius audit both carried 1.0.

## B6. Beats, voice, segments — shared, unchanged

`segment_beats_scenes`, the TTS path and `build_segments` are the default
system's code, imported and run as-is. The lab does not fork the spine.

---

# PART C — side by side

> Rewritten 2026-09-19. The previous table's "Claude lab" column said *none*
> for tall panels, blank crops, script budget, script QC and hold control, and
> "one prompt, dedup only" for placement. Claude+ added every one of those, so
> the old table now overstates the gap everywhere **except panel cutting** —
> which is the one place it was, and remains, real.

| Decision | Default (Gemini) | Claude+ lab |
|---|---|---|
| **Panel cutting** | **4 passes, coverage-gated 0.85, bubble/figure anchors, trim-to-content, ~25 tuned thresholds** | 1 vision call + coverage gate 0.85 + retry + gap recovery + blank drop + YOLO fallback |
| **Tall panels** | **recursion → vision beats → moment slices → valley cuts** | `_split_tall` — valley cuts only |
| Blank/junk crops | density threshold + archive | the **same** `_is_blank_crop`, reused |
| OCR | exact, punctuation kept | exact, **plus `ocr_confidence`** which feeds placement |
| Description form | max 50 words, must open with an action verb | audited for verb/hedging/OCR-restating, **static + credits panels exempt** |
| Script length | computed budget 12/panel + 7/dialogue | `word_budget` + `scene_budget`, as a **range** (0.50–0.90) not a point |
| Script QC | critique pass, 5 issue types | critique + revise, incl. `missing_worldbuilding`; **under-spend computed in code** |
| Placement | global DP, embeddings, junk filter, importance, provenance | global DP, **OCR-confidence weights**, junk filter, `PROVENANCE_COST` 0.3 (swept) |
| Hold control | `HOLD_PENALTY` 0.06, `HOLD_CAP_S` 12s | the same constants, plus `MIN_VISUAL_SEC` 1.4 for progression |
| Crop trigger | only when narration hits a detail keyword | planned per placement, then scored |
| Crop scoring | `crop_score.choose_crop` | **the same** `crop_score.choose_crop` |
| Crop timing | after the script, driven by it | after the script, driven by it |
| Beats / voice / segments | `beat_segmenter` + `build_segments` | **the same modules, imported** |

**The honest summary:** these two systems have converged. Reading, scripting,
placement, cropping and timing are now either the same code or the same ideas
with different tuning. **Panel cutting is the one stage that never converged**,
and it is the stage where the default has four passes and roughly twenty-five
tuned thresholds against the lab's single call plus a validation gate.

---

# PART C2 — the blend (`splitter="yolo"`)

The operator's read after chapter 352 was: *Claude's OCR, description, script
placement and on-screen timing are better; the default's panels are better.*
Part C says why that is exactly what you would expect.

**The blend already exists and needs no new code.** `split_with_yolo` shells out
to `panel-split/split_panels.py` with the same argv, cwd and env that
`ingest.py` uses — so running the lab with `splitter="yolo"` gives production's
panels, unmodified, feeding Claude's reading, scripting, DP placement and
crop-after-script.

| Stage | Blend uses | Why |
|---|---|---|
| Panel cutting | **default (YOLO, 4-pass)** | the stage that never converged, and the default wins it |
| Read / OCR | Claude+ | confidence fields feed placement |
| Script | Claude+ | budgeted, critiqued, world-building aware |
| Placement | Claude+ DP | provenance-constrained |
| Crop | Claude+ → shared scorer | planned after the line exists |
| Beats / voice / segments | shared | identical in both |

Cost note: the split stage costs **$0** in the blend (YOLO is a local model, no
API calls), so a blend run is *cheaper* than a `claude`-split run of the same
chapter, not more expensive.

The board column that changes is **Panel**. `/panelimg/{pid}` serves the panel
PNG the splitter produced, so the Panel column is a direct view of the splitter
— which is precisely the column the operator wanted back.

---

# PART D — where to go from here

Ranked by expected effect on output quality.

1. **Move the crop decision after the script.** Port `should_crop_close` and
   pass Claude the beats assigned to each panel. This is a pipeline reordering,
   not a prompt tweak, and no amount of prompt tightening substitutes for it.
2. **Port the word budget and density rule** into the script prompt verbatim,
   with the banned vocabulary and the reported-speech requirement.
3. **Add a critique/revise pass** to the lab script stage — the default's five
   issue types are model-agnostic and would transfer unchanged.
4. **Port the 50-word cap and the forced action-verb opening** into the read
   prompt, with the banned openers.
5. **Give the Claude splitter a coverage check.** Compute ink coverage of the
   returned boxes and re-prompt (or fall back to gutter recovery) below 0.85 —
   currently nothing tells Claude it missed art.
6. **Enforce the ordering the placement prompt asks for.** A monotonicity check
   with a repair step would catch what the instruction cannot guarantee.
7. **Port `CROP_MIN_AREA = 0.12`** so a sliver crop is impossible.
8. **Tall-panel handling.** The lab has no equivalent of `TALL_RATIO` /
   moment-slicing; on webtoon-style strips this is likely the largest single
   source of difference in panel counts.

## A note on what the comparison currently measures

The default encodes a long tail of specific fixes, each traceable to something
that went wrong: the 50-word cap, the banned openers, the dialogue-fidelity
rule, the crop contract, the coverage gate, the finite over-hold penalty. The
lab's prompts are first drafts.

So the present result measures **tuned Gemini against untuned Claude**, not
Gemini against Claude. Items 1–4 above would make it closer to a fair test.

---

# PART E — running it: autopilot, review, spend (added 2026-10-03)

Operator runbook with every knob: `docs/AUTOPILOT_RUNBOOK.md`. This part
explains the design and why.

## E1. Chapter Autopilot — `review_ui/autopilot.py`

Ingests new chapters by itself so they appear in **Projects** ready to review.
It reuses the ordinary ingest queue, one chapter at a time; it is not a second
pipeline.

- **What it picks:** every watchlist (Tracker) series. Each series starts at its
  **latest 3 chapters**, fixed the first time autopilot sees the series (stored
  in `start_from`, so the window does not slide), then continues in story order
  through every new release.
- **Order — round robin:** the next chapter goes to the series autopilot has
  made the fewest chapters for; ties go to watchlist order (tier, then rank).
  So each pass serves the top-ranked series first, then the rest.
- **When:** a scheduler thread (`server._scheduler_loop`, every 10 min, first
  pass 60 s after boot) starts at most one chapter, only if the ingest queue is
  empty, today's count is under `per_day` (default **4**, America/New_York day),
  and one chapter's estimate fits both autopilot's budget and the site cap (E4).
- **Engine:** Gemini only, on the Flex tier (`AUTOPILOT_TIER`, default `flex`).
- **Never repeat:** a ledger in `projects/_autopilot.json` records every chapter
  autopilot queued and every manual ingest that finished. It survives project
  deletes, so a chapter you deleted on purpose is not re-made. Chapters deleted
  before 2026-10-03 predate the ledger and are not in it.
- **Failures:** the first failure waits an hour before a retry; a second blocks
  the series ("needs you") with the error, so story order is never skipped.
- **Never hijacks the board:** autopilot jobs are tagged `source: autopilot`.
  The page never adopts or auto-opens them, so a chapter finishing in the
  background cannot switch the board away from the one you are reviewing.
- **Card (Ingest page):** ON/OFF, next chapter, why it's waiting, today's count
  (editable), spend vs budget and cap, last chapter, last check, undo; per-series
  pause / resume / retry; ↻ check now re-reads every series page (free).

## E2. Review inbox — Projects

Status is derived from files the studio already writes, with no new state:
**Ready for review** (ingested, not approved) → **Approved** (`storyboard.json`)
→ **Rendering** (finalize job running) → **Rendered** (an `.mp4` in
`exports/`) → **Published** (a `publishes.json` result is `published`) →
**Archived**. Rows show an `auto` badge, the free story-check count
(`validation.json`, run in `rules` mode after every ingest), and Claude-lab
copies are labelled so they no longer read as duplicate chapters. The sidebar
shows "N to review". Approve still renders and exports automatically.

## E3. Stop and resume — everything

| What | Stop | Resume |
|---|---|---|
| All of autopilot | card → Switch off (nothing new starts) | Switch on |
| One series | card → ⏸ pause | ▶ resume |
| A running ingest | Logs → ⏸ / ⏹ (lands between pipeline steps) | Logs → ▶ |
| A stopped / failed ingest | — | Logs → ▶ re-queues the same record, reusing cached stages |
| An approve-render | board's ⏹ | Logs → ▶ (open that project first) |

A chapter you stopped is never re-picked by autopilot on its own; its series
shows "stopped by you" until ▶ or ↻ retry.

What happens without you:
- **Deploy/restart:** an ingest cut off in the last 6 h becomes `interrupted`
  and is re-queued at startup (`RESUME_MAX = 2`). Older or owner-stopped ones
  are written off as before. Railway volumes cannot attach to two containers,
  so an old and a new server never run the same job.
- **Spend cap hit:** the ingest becomes `budget_paused` (not an error) and is
  re-queued first after midnight ET.
- **Archive:** a project with a published video is archived and its folder is
  deleted **14 days** later unless you press **Keep**; Unarchive cancels it.
  The open project is never deleted. Exports are separately kept 7 days.

## E4. Spend limits

- **Site cap** `MAX_DAILY_SPEND_USD` = **$10** (Railway). Checked before every
  paid call, every job, manual or automatic: the hard stop.
- **Autopilot budget** `budget_usd` = **$6** (card). Counted from autopilot
  jobs' own metered spend today; decides whether a chapter *starts*, so a
  chapter started under budget can finish slightly above it.
- **Estimate** used for both checks: mean metered cost of the last 5 autopilot
  chapters ($0.80 until there are any).

## E5. Metering — real tokens at published prices (`usage.py`)

The fix of 2026-10-03, and why the earlier numbers were wrong:

- The Interactions API (our `AQ.` key) reports `usage.total_input_tokens /
  total_output_tokens / total_thought_tokens / total_cached_tokens` plus
  `service_tier`. The readers looked for generateContent names, got 0, and
  `Meter.tokens(0, 0)` counted that as a real report: **3,145 calls from
  2026-09-12 were logged at $0.00.** Earlier calls were priced at a flat
  $0.001 each.
- `usage.parse_gemini_usage` reads both shapes. **Thinking tokens are billed
  as output** (a one-word answer used 57–78 thinking tokens), cached input at
  the caching rate, Batch/Flex at half.
- A zero report is never "free": the call keeps its estimate and is logged
  `metered: false`.
- Each log row carries `prompt_tokens`, `output_tokens`, `thought_tokens`,
  `service_tier`; Logs → API usage shows them per call.
- Published Standard rates (ai.google.dev, read 2026-10-03), per 1M tokens:

| Model | In | Out (incl. thinking) | Flex / Batch |
|---|---|---|---|
| gemini-3.8-flash | $0.75 | $3.75 | half; **doubles 2027-01-01** (applied automatically) |
| gemini-3.5-flash | $1.50 | $9.00 | half |
| gemini-3.1-pro-preview (≤200k) | $2.00 | $12.00 | — |
| gemini-2.5-flash (crop planner) | $0.30 | $2.50 | — |
| gemini-embedding-2 | $0.20 | — | no Flex |
| gemini-3.8-flash-tts | $0.50 text | $9.00 audio | doubles 2027-01-01 |

Env `PRICE_*` overrides still win. Live proof call (`meter-check-20261003`):
8 in / 65 out (64 thinking) = **$0.000597**, exactly the sheet price.

## E6. What a chapter costs

Calibrated to the owner's Google bill ($53 to 2026-10-03): those ~8,000
under-counted Gemini calls really averaged **$0.0053** each on 3.5 Flash. A
median chapter is about 57 segments, 8 minutes of video, 162 describe+narrate
calls, 7,900 voice characters.

| Setup | Per chapter | 4 a day: day / month / year |
|---|---|---|
| 3.5 Flash, Standard (before 2026-10-03) | ~$1.15 | $4.88 / $146 / $1,782 |
| 3.8 Flash, Standard (manual ingests now) | ~$0.68 | $2.98 / $89 / $1,088 |
| **3.8 Flash, Flex (autopilot now)** | **~$0.49** | **$2.22 / $67 / $811** |

Per chapter = describe + narrate + voice (~$0.14) + matching (~$0.005) +
server (~$0.15). Days include ~$8/month fixed Railway (memory is its largest
line). Not included: Upload-Post ($24/month, shared with Scrapper). These are
estimates until the first autopilot chapters are metered end to end; the card's
"~$ a chapter" switches to the measured average by itself. Disk is ~250 MB a
chapter against a 28 GB volume, so archiving matters.

## E7. Audit of the first autopilot chapters (2026-10-04)

Seven projects were compared on the server: six autopilot chapters and Fog
Land ch.2 (manual, 2026-10-02, 3.5 Flash).

**Same as the manual path:** autopilot runs the same `ingest.run_ingest` code.
The only differences are the model (3.8 Flash, now used by manual ingests too)
and the Flex tier, which changes queueing and price, not output. No panel
batching is used. No describe call failed. Every *usable* panel reached the
timeline (0–1 per chapter left off).

**Crops are not lost.** Real close-up crops per chapter: 6–15 on autopilot vs
11 on Fog Land; the planner fired on 11–21 vision crops per chapter. Files
missing from autopilot folders (`exports/`, `assets/`, `storyboard.json`) are
created by Approve, so their absence before review is expected.

**Two real gaps, both older than autopilot:**

1. **Dialogue on junk panels never reaches the script.** `narrate.load_panels`
   drops `is_junk_panel` panels before writing, and a panel whose picture is
   only a speech bubble or a text card has no subject or scene word, so it is
   junk. Its OCR'd dialogue is lost. Measured share of dialogue words sitting
   on junk panels: Mount Hua ch.180 **27%** (30 panels), Extra's Academy 114
   11%, Iron-Blooded 180 10%, Regressed Mercenary 96 8%, Stellar 129 5%, Fog
   Land 2 4%, Fated Villain 358 2%. Worst on Asura, where the Sept 23 splitter
   cuts floating bubbles out as separate panels.
2. **A wording override drops real scenes.** `_ABSTRACT_OVERRIDE` includes
   `(running|curving|…|extending) (down|along|across…)…(edge|frame|panel…)`,
   meant for stray gutter lines. Mount Hua's 900×3994 establishing shot ("…
   dense foliage and rocks **extending down the panel**") matched it and was
   dropped although it names a slope, rocks, night and a building.

Also noted: tiny slivers (7–64 px) come back with empty descriptions; they are
correctly junk. A very tall panel (AR ≈ 4.4) ships as one card; the
tall-panel slicing of the legacy splitter does not run on the default path.

## E8. Image check — bubbles, blanks and credits (2026-10-04)

**Why:** the owner's Murim 44 board showed speech-bubble cards ("I THOUGHT IT
WAS JUST A PERFORMANCE", "ELDER,") and near-black strips (embers, a red slash,
rain in darkness) each with its own narration line and 6–9 s on screen. They
passed the wording-only junk filter because the AI's descriptions used keep
words: "spiky **burst** bubble", "blood-red **energy**", "**glowing** embers",
"**rain** streaks". The QA pass had no rule about what a panel *looks* like.

**How (`panel_stats.py`, run by ingest right after describe; free):** a 300 px
thumbnail gives `ink` (share of pixels clearly unlike the dominant colour),
`std` (luma contrast) and `luma`; with the OCR and description:

| Role | Rule | Off the timeline? |
|---|---|---|
| `credits` | description says credits/logo/title card, OCR has a site or scanlation credit, or a page-1 panel ≥25% wider/narrower than the chapter (the aggregator banner) | yes, never narrated |
| `fragment` | thinner than 40 px, or no text **and** contrast < 20 **and** ink < 0.10 | yes |
| `bubble` | has text, no person/creature and no place in the description (quoted lettering and "no …" clauses ignored), and ink < 0.16 or the description calls it text/lettering/bubble | yes — **its dialogue is handed to the next art panel on the same page** (text first), else the previous one, before the script is written |
| `art` | everything else | yes if ink ≥ 0.30 etc. — wording rules can't drop it |

Calibrated on Murim 44 (145 panels): fragments had contrast 0–13, real story
panels never below 34; bubble cards ink 0.09–0.13; real art ink 0.21–0.62.
Dry run on all 8 live chapters: 71 panels go off (23 credits, 29 fragments,
19 bubbles) and 10 real scenes come back (the Mount Hua
mountain shot and vault door, a Murim rooftop leap, marching soldiers…).

**QA:** `validator.rule_findings` now raises a **high** finding when a bubble,
fragment or credits panel is on screen in the video (e.g. ticked back in by
hand), so it shows on Check before review.

**Effect on the script:** bubble dialogue now reaches narration through the
neighbouring panel (Mount Hua 180: 29 bubbles' dialogue; Extra's 114: 24),
instead of being dropped with the panel.

## E9. Story research — a sourced Series Bible for every series (2026-10-04)

**Before:** the Series Bible (`series_bible.py`; injected by ingest into describe,
narrate and SEO) existed only for Murim Psychopath, hand-written on 2026-10-01.
Every other series' chapters were written with no cast, pronouns or world rules.

**Now (`series_research.py` + `review_ui/research_service.py`):**
1. **Grounded research:** Gemini 3.8 Flash `generateContent` with the
   `google_search` tool (works with our `AQ.` key via the `X-goog-api-key`
   header). The answer comes back with `groundingChunks` (sources) and
   `groundingSupports` (which sentence each source backs).
2. **Sources resolved and graded:** official (webtoons.com, Naver, Kakao,
   publishers), trusted (Wikipedia, Anime News Network, MangaUpdates, AniList,
   MAL, Novel Updates), **wiki** (fandom), low (Reddit, YouTube, social).
3. **Structured** by a second call (no tools) into the bible schema, each
   entry citing source numbers. **Owner rules:** characters need an official,
   trusted or wiki source (fan wikis allowed for names, pronouns and looks; the
   panels' own image + OCR check is the agreement); world and story facts need
   official or trusted, otherwise they're kept as "unverified" and not fed to
   the narrator; disputes between sources are kept for the owner.
4. **Merge:** what's already there wins (Murim's hand-written bible, owner
   edits marked `origin: owner`); research only adds characters (an alias match
   counts as already listed) and fills empty world fields.
5. **Saved under every mirror's slug**, so ingest finds it whichever source a
   chapter comes from (Asura slugs drop the rotating code; WEBTOON slugs keep
   the title number, e.g. `the-stellar-swordmaster-5988`).
6. **When:** automatically on the first chapter of a series with no bible
   (ingest, before describe); on demand per series (📖 cast → ↻ research
   again) or for every series missing one; metered (~$0.02 a series).
7. **Learns from our chapters:** after each script, capitalised names it keeps
   using that the bible lacks become `suggested_characters` for the owner to
   confirm (never added by themselves).

UI: Autopilot card → All series → **📖 cast** (cast table with origin and
source grades, world, story so far, disputes, suggestions, sources; ↻ research,
✏️ edit). Moves to the Series board in step 1. Routes: `GET/POST /api/series/bible`,
`POST /api/series/research` (one series, or `missing_only`). Tests:
`test_series_research.py` 28/28.

## E10. Series board — calendar and watchlist merged (2026-10-04)

Tracker now has **📚 Series** (default) and **✏️ Manage list** (add a title,
sources, tier/rank). One card per series: cover, tier (**Make now / Next up /
Watching** — display names; stored ids unchanged), rank, source, latest chapter
and date, autopilot state, **Made · Next · N more in the plan**, **earlier
chapters not planned**, **new since last made**, research status (📖 N
characters, disputes, new names), and controls (▶ make now, ⏸/▶, ↻ retry,
📖 cast, chapters ▾ → Ingest this chapter / **plan backfill from here**).

- "Made" = projects on disk **plus the autopilot ledger**, so a made-then-
  deleted chapter stays made (the old "not made yet" counted every chapter
  since ch.1 and ignored the ledger).
- "Left in the plan" = autopilot's remaining chapters from its start window.
- **Backfill** (owner rule: never by default): `POST /api/autopilot/backfill`
  moves that series' start back to ch.N; autopilot then makes N, N+1, … in story
  order before that series' newer chapters.
- Speed: `/api/series/board` carries no chapter arrays; a card's chapter list
  comes from `/api/series/chapters` when "chapters ▾" is opened; covers load
  lazily.
- Filters: All · Next up for autopilot · New since last made · Needs you ·
  Paused · Caught up. Sort: latest release (calendar) or plan order.
Tests: `test_series_board.py` 13/13.

## E11. Logs & Activity — like Scrapper's (2026-10-04)

- **🔴 Live** (default): one line per event, `time [kind] message`, coloured
  by level; filters (Autopilot · Ingest · Render · Research · Errors) and
  search; follows new lines unless you scroll up. Source:
  `review_ui/events.py` → `projects/_events.jsonl` (newest 5,000), read with
  `GET /api/events?after=<id>` every 3 s (polling, no held-open connections).
  Emitted by: ingest (start, each stage, image check, research, finish with
  cost / paused / stopped / failed), autopilot decisions in plain words
  (`autopilot._SAY`), approve → re-voice → render → export, research per
  series, archive, scheduler failures.
- **⏳ Jobs**: grouped Running · Waiting · Finished today · Earlier
  (`GET /api/logs/jobs`), each with a readable chapter name, kind icon, stage,
  progress, elapsed, cost (from the usage log), "(deleted)" when the project is
  gone; export records labelled as exports. Pause / ▶ resume / two-tap ⏹ / 🗑.
- **💰 Spend**: today vs the $10 site cap and the $6 autopilot budget, the month
  by day, by provider, recent chapter costs, and every call with real tokens
  (`GET /api/spend`).
- **🗒 What changed**: unchanged.
- **Jobs bar on every page** (`theme.JOBS_JS`, board and Review): now reads the
  light `GET /api/jobsbar` (in-memory) instead of two full listings every 4 s;
  readable names, ⏸, ▶ resume for restart/cap-waiting jobs, **two-tap Stop**.
Tests: `test_logs.py` 18/18.


## E12. ⚙️ Settings & Channels (2026-10-04)

One page (left menu → ⚙️ Settings & Channels) showing every studio setting and its current state. It reads from `GET /api/settings/overview` and saves through `POST /api/settings`. The settings are stored in `projects/_studio_settings.json` and handled by `review_ui/studio_settings.py`.

| Card | Shows | Change it |
|---|---|---|
| 🟢 Connection | Deployed commit; scheduler running, its last check and last error | — |
| 💰 Spending | Site spend today against the $10 hard stop; autopilot spend against its $6 budget; the date the prices were read | Autopilot budget: the Autopilot card. Site cap: Railway `MAX_DAILY_SPEND_USD` |
| 🤖 Autopilot | On/off, chapters per day, the starting window, model and tier | The Autopilot card on Ingest |
| 🔗 Channels | Upload-Post status and the connected accounts | Checkboxes for which accounts new videos post to, plus privacy → **save** |
| 🎬 Export | Video speed (default **1.25×**) | 1.0 / 1.1 / 1.25 / 1.5 → **save**. Locked when the Railway `EXPORT_SPEED` variable is set (that variable wins) |
| 🎙️ Voice | The studio narrator and style | Ingest. Approving a chapter re-voices it if its voice is out of date (E-section above) |
| 🗄️ Storage | Disk use, the largest projects, 7-day export retention, archive deletion 14 days after publishing | — |
| 💾 Backups | A per-chapter download link. A Railway volume restore replaces the whole disk | — |

**Posting defaults:**
- New videos post to 🦩 Flamingo Remix (`mk:youtube`).
- Privacy starts **private** and becomes public only once the owner picks "public" and presses **save**. Nothing is made public by omission, which is a standing guarantee and a precondition for rights gating.
- Every video still needs its own Publish press, because the posting schedule (step 5) is off.

Tests: `test_settings.py`.
