# Pipeline Handbook — the default (Gemini) system vs the Claude lab

Every decision point, threshold and prompt rule in both pipelines, read out of
the code rather than remembered. Written to answer one question: where is the
Claude lab under-specified compared to the system that has been tuned for
months, and what is worth porting across.

Source files: `scraper.py`, `panel-split/split_panels.py`,
`panel-split/vision_segment.py`, `panel-describe/describe.py`, `narrate.py`,
`beat_segmenter.py`, `matcher.py`, `shot_planner.py`,
`hyperframes/segments.py`, `review_ui/claude_lab.py`,
`review_ui/claude_pipeline.py`.

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
- **Voice** (`server._synth_rest`). Chirp REST + API key with certifi's CA
  bundle. Results cached by `hash(voice + text)` in `projects/_ttscache`, so an
  unchanged sentence re-synthesises for free. Timeline rhythm is scene-aware:
  **0.6s** pause at a scene boundary, **0.25s** within a scene, **0.35s** flat
  when there is no provenance.

---

# PART A — the default (Gemini) system

## A1. Cut into panels — `split_panels.py`

**Coverage-gated, four passes, with anchor back-stops.** The controlling idea is
that no pass is trusted on its own; each one is checked against how much of the
page's *ink* has been covered.

`detect_panels()` runs in this order:

1. **YOLO** (`conf=0.3`). Each box passes `_apply_bleed_guard` (expands up to
   30px to avoid clipping art) and must be ≥ `MIN_PANEL_SIZE = 80` px on both
   axes.
2. **Geometric gutter split** over any content bands YOLO left uncovered.
3. **Anchors force-include what both missed** — speech bubbles
   (`BUBBLE_MIN_AREA = 4000` px², `BUBBLE_MAX_AREA_FRAC = 0.25`) and detected
   figures. An anchor counts as safe if ≥ `ANCHOR_COVERED_FRAC = 0.6` of it
   lies inside a kept box.
4. **Fail-open**: remaining content bands ship as density crops.

Coverage control: `COVERAGE_TARGET = 0.85` — below this, gap recovery runs.
A gap band must have ≥ `GAP_MIN_DENSITY = 0.04` ink and be ≥ `GAP_MIN_H = 120`
px to matter. Per-stage coverage is recorded in stats and surfaced on the board
as `min / mean / worst_page / pages_below_85`.

**Gutter detection:** background colour estimated per page;
`BG_COLOR_TOLERANCE = 10`; a row/col is gutter if ≥ `GUTTER_FRACTION = 0.985`
of it matches background; a real gap needs `MIN_GUTTER_RUN = 8` consecutive
such rows; `EDGE_MARGIN = 4` px trimmed off each cut to avoid gutter bleed.

**Content trim:** `TRIM_TO_CONTENT = True`, `CONTENT_PAD = 6` px kept around
content, `CONTENT_LINE_FRAC = 0.01` for a row/col to count as content.

**Blank handling:** `BLANK_DENSITY_THRESHOLD = 0.015`; blanks are *archived*
(`ARCHIVE_BLANKS = True`), moved to a subfolder rather than deleted.

### Layer 2 — sub-shots inside a tall panel (`_layer2_shots`)

A panel is a candidate when height/width ≥ `TALL_RATIO = 1.8`. Then, in order:

1. **Internal gutters?** Split on them and **recurse**. (Added after 7:1 pages
   reached the board as single untouched crops.)
2. **Gutterless tall → vision segmentation** (`vision_segment.py`), if
   height/width ≥ `MIN_RATIO_FOR_VISION = 2.2`. The model is asked to list every
   caption/narration box top-to-bottom with `y_center`, verbatim `text`, and a
   one-sentence description of the art around it. Cuts are placed at the
   **midpoints between consecutive caption centres**. `temperature = 0.0`.
   Requires `MIN_BEATS = 2` or it returns `None`; slivers thinner than
   `MIN_BEAT_FRAC = 0.02` of height are dropped.
   **Fail-safe:** any error, missing key, or single-beat result returns `None`
   and the caller keeps its geometric behaviour — never worse than before.
   Because the same call returns OCR and description, these sub-crops arrive
   **pre-described**.
3. **Moment slicing** otherwise: bubble clusters separated by
   `MOMENT_GAP = 300` px, cut at the lowest-ink row between clusters,
   `SLICE_MIN_H = 320` px minimum.
4. **Bubble-less extreme tall** (`NO_DIALOGUE_AR = 3.0`): ink-valley cuts into
   pieces of roughly `VALLEY_TARGET_AR = 2.2`.

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

## A3. Write the script — `narrate.py`

The most constrained stage in the system, and a **two-pass** one.

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
`SEMANTIC_SIMILARITY`), disk-cached, `EMBED_BATCH = 32`, `EMBED_RETRIES = 4`,
transient-error detection, and a one-off probe that drops to per-text embedding
if the installed SDK does not honour batching. Deterministic lexical fallback
(`OCR_WEIGHT = 0.55`, `DESC_WEIGHT = 0.45`) so it runs with no external service.

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

# PART B — the Claude lab (`claude_lab.py`, `claude_pipeline.py`)

## B1. Cut into panels — switchable

**Option A — Claude cuts (`split_with_claude`).** One vision call per page.
Pages downscaled to `PAGE_MAX_PX = 1400`. The prompt asks for panel boxes in
reading order plus a `kind` of `art | text | credits`, instructs that bubbles
overlapping borders be included, and says it is better to include gutter than to
clip art or text.

Validation is `_sane_box()`: clamp to [0,1], sort so x0<x1, reject anything
under 5% wide or 1% tall. `MAX_PANELS_PER_PAGE = 14`. Boxes sorted by
(y0, x0). Crops taken from the **original** resolution.

**Absent compared to the default:** no coverage target, no gap recovery, no
bubble/figure anchors, no background/gutter analysis, no trim-to-content, no
blank detection or archiving, no minimum pixel size, no tall-panel recursion, no
moment slicing, no valley cuts.

**Option B — YOLO cuts (`split_with_yolo`).** Runs `split_panels.py` exactly as
ingest does, so this half is identical to the default.

## B2. Read each panel — `claude_pipeline.describe`

`PANELS_PER_CALL = 6` images per call, each labelled `PANEL n:`. Returns per
panel: `ocr`, `description`, `crop`, `is_credits`, `subject`, `importance` (1–5).

Crop validation is `_clean_crop()`: clamp, reject under 5% in either axis, else
accept. **No `CROP_MIN_AREA = 0.12` floor.**

Checkpointed after every batch and resumable — a restart skips panels already
read rather than paying twice.

**Compared to the default's describe prompt:** no word cap, no forced
action-verb opening, no banned openers, one example instead of four, and OCR is
not declared the primary match signal. It does add `is_credits`, `subject` and
`importance`, which the default has no equivalent of at this stage.

**The structural difference:** the crop is chosen **here** — in the same call as
the description, before any narration exists. It therefore cannot follow the
line, because the line has not been written.

## B3. Write the script — `claude_pipeline.script`

`SCRIPT_CHUNK = 40` panels per call. A rolling tail passes the last unit's
closing ~400 characters as continuation context. Returns units with
`covers_panels` hints. Units renumbered densely 0..n-1.

Eight bullet rules: story order, past tense third person, numbered units of one
or two sentences, cover the whole chapter, skip credits/title/SFX panels, don't
narrate the merely visible, never invent, don't address the viewer or tease.

**Absent compared to the default:** no word budget of any kind, no voice anchor,
no style anchor, no banned vocabulary, no dialogue-fidelity rule, no
reported-speech requirement, no density rule, no global beatsheet, **and no
critique/revise pass at all.**

## B4. Place lines on panels — `claude_pipeline.place`

**One call** carrying every unit and every panel. `max_tokens = 32000`, so it
streams (see below).

Rules given: every unit gets at least one panel; panels stay in reading order
across the chapter; choose the panel that *shows* what the line is about, not
one merely on the same topic; a unit may hold several consecutive panels; a
panel may go unused; never assign credits, title cards or pure SFX panels; a
reveal's panel must not precede the unit that sets it up.

Post-processing: a `used` set prevents one panel going to two units. Unknown
panel numbers dropped.

**Absent compared to the matcher:** no DP, no scoring, no embeddings, no hold
penalty or hold cap, no junk filter, no importance bonus, no provenance
constraint, no enforcement of the monotonic ordering it was asked for — the
instruction is stated but never checked.

## B5. Beats, voice, segments

Beats and voice are **identical** to the default — `segment_beats_scenes` and
`_synth_rest`, unchanged.

`claude_lab._build` lays the placement onto the timeline: each unit's panels
split that unit's beat window evenly, the beat covering a slice's midpoint
supplies the row's text, then shot ends are snapped to the next shot's start —
the same exact-tiling rule as `build_timeline`. `focus_source = "claude"`.

> The `claude_pipeline.timing` stage (Claude proposing seconds and camera moves)
> exists but the lab does **not** use it — lab timing comes from real TTS audio.
> It is only used by the older sidecar comparison path.

## B6. One shared fix worth recording

`validator._call_claude` now streams whenever `max_tokens ≥ 8000` and collapses
the result with `get_final_message()`. The placement stage failed in production
with *"Streaming is required for operations that may take longer than 10
minutes"* — after the read and script stages had already been paid for.

---

# PART C — side by side

| Decision | Default (Gemini) | Claude lab |
|---|---|---|
| Panel cutting | 4 passes, coverage-gated at 0.85, anchors, ~25 thresholds | 1 prompt, clamp + 5%/1% reject, cap 14/page |
| Tall panels | recursion → vision beats → moment slices → valley cuts | none |
| Blank/junk crops | density threshold + archive | none |
| OCR | "highest-priority match signal", exact, punctuation kept | exact, joined — priority not stated |
| Description length | **MAX 50 words** | "one or two sentences" |
| Description form | **must** open with action verb; 3 banned openers | free |
| Script length | **computed budget** 12/panel + 7/dialogue, 40–220 | none |
| Script rules | 9 mandatory + banned vocabulary + voice anchor | 8 bullets |
| Script QC | **critique pass** (5 issue types) + targeted revision | none |
| Placement | global DP, embeddings, junk filter, importance, provenance | one prompt, dedup only |
| Hold control | `HOLD_PENALTY` 0.06, `MAX_HOLD` 5, `HOLD_CAP_S` 12s | none |
| Crop trigger | **only** when narration hits a detail keyword | every panel, always |
| Crop input | panel image **+ the beats assigned to it** | panel image alone |
| Crop output | box + framing_mode + reason + confidence | box only |
| Crop floor | `CROP_MIN_AREA` 0.12 | 5% per axis |
| Crop timing | **after** the script, driven by it | **before** the script exists |

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
