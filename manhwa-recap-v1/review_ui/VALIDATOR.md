# The Check tab — how the validator works

Reference for `validator.py`, the routes in `server.py`, and the Check drawer
in `storyboard.py`. Kept in the repo deliberately: the last handbook lived in a
scratchpad and rotted within a week.

---

## 1. Why it exists

The board is assembled by five machines in a row — splitter, OCR, vision
describer, matcher, segmenter — and none of them is 100% accurate. Until now
the only thing standing between a wrong row and a published video was the owner
reading all 138 rows by hand, every chapter.

The matcher is the weak link, and it fails in a specific way. It matches on
embedding similarity, so it does not put a line on a *random* panel — it puts
it on a panel that is **topically related but wrong**. Those are exactly the
errors that survive a tired skim, because nothing about the row looks broken.

The goal is not to replace the owner's review. It is to make sure that by the
time the owner looks, everything mechanically checkable has already been
checked, and the handful of rows that need human judgement are marked.

---

## 2. What it checks

The five board columns, as data rather than as rendered HTML — a finding has to
point at something fixable, not at a formatting artefact.

| Column | Source file | What can go wrong |
|---|---|---|
| Panel | `crops/<panel_id>.png` | flash panels, panels never shown |
| System OCR | `descriptions.json` → `ocr_text` | scanlation credits read as story text |
| System description | `descriptions.json` → `visual_description` | empty, generic, or describes a different panel |
| Script placement | `script.json` → `scene_id`/`panel_ids` | **line on the wrong panel**, story-order jumps, units with no panel |
| On-screen timing | `segments.json` → `dur`/`beats` | sub-second flashes, long dead air |

---

## 3. The chain

```
splitter/OCR/vision/matcher → 1. RULES → 2. CLAUDE text → 3. CLAUDE vision → owner
                                free       batched          flagged rows only
```

The ordering is the whole design: each pass removes work from the next, and the
expensive pass runs last and smallest.

### Pass 1 — Rules (`rule_findings`)

Free, instant, deterministic, no credentials. Everything with an exact answer
is computed rather than paid for.

- **Flash panel** — in-video panel on screen under `FLASH_SEC` (1.0s).
- **Dead air** — silent panel held `LONG_SILENT_HOLD_SEC` (12s) or more.
- **Story-order inversion** — a panel carrying an earlier unit than the panel
  above it, i.e. the recap runs backwards.
- **Credit page carrying narration** — OCR matching 2+ scanlation markers
  (`discord.gg`, `…Scans`, `TL`/`PR`/`QC`) while a line is placed on it. A
  credit page already left out is the pipeline working, and is *not* reported.
- **Missing or failed description** — nothing for the matcher to have matched
  on, so any line placed there was placed blind.
- **Coverage** — a narration unit with no panel in the final video.

> **A check deliberately absent.** "Can the narration be said in the time
> available" is not checkable, because it cannot fail: the timeline is *derived
> from* the TTS audio, so words always fit by construction. An earlier version
> checked words-per-second and raised 27 false alarms on a clean chapter,
> because folded panels each carry a *copy* of their shared beat's full text —
> six panels sharing one 24-word beat each looked like they had to speak 24
> words in a 0.76s slice. Removed; the regression is frozen as a test.

### Pass 2 — Claude, text (`claude_text_findings`)

The judgement rules cannot make: **does this line belong to this panel?**

Batched at `BATCH_ROWS` (25) per call — not for throughput, but because an
out-of-place line is usually recognisable only by the fact that the panel it
*does* belong to is sitting two rows away. Claude needs the neighbourhood.

- Structured output (`json_schema`) so the reply is parseable by construction.
- Instructions sent as a cacheable prefix; only the rows after it change.
- Reports four things: a line on the wrong panel, a description contradicting
  its own OCR, a description too generic to have driven a real match, and
  credits carrying narration.
- Told explicitly *not* to repeat the arithmetic pass 1 already did, and that
  an uncertain row is not a finding — precision over recall, because a false
  alarm costs exactly the reading time this is meant to save.
- A finding naming a row outside the batch is dropped as a hallucination.

### Pass 3 — Claude, vision (`claude_vision_findings`)

Passes 1 and 2 both reason about a *description* of the panel. If that
description is wrong, both are reasoning from a bad premise. Pass 3 is the one
link in the chain that is not downstream of it: it opens the actual crop.

- Runs **only** on rows already flagged, capped at `MAX_VISION_ROWS` (24).
- Images downscaled to `VISION_MAX_PX` (1200px) JPEG — a 760×1598 crop tells
  Claude nothing extra and costs tokens for the privilege.
- Returns `confirmed` / `cleared` / `unclear`, plus a corrected description when
  the automated one was wrong.
- A **cleared** finding is demoted to low and annotated — never deleted.
  Otherwise the pass is invisible exactly when it does its best work.
- Rows past the cap keep their finding and are reported as *unchecked*, not as
  clean.

---

## 4. Why it is its own tab

1. **It is a mode, not a row action.** It operates on the whole board at once,
   like Ingest or Tracker. The rail is where whole-board modes already live;
   putting it anywhere else would be inconsistent.
2. **Findings are a list and need room.** The board table is already six dense
   columns. A findings list with issue, fix and provenance does not fit in a
   cell.
3. **It costs money, so it needs a deliberate surface.** A drawer with a mode
   picker and a confirm is the right shape for something you choose to spend
   on. An ambient check that fired automatically would spend without being
   asked.
4. **It is advisory, and should look advisory.** A separate panel reads as a
   second opinion. Findings written into the board itself would read as the
   system having changed your work.

The compromise is two levels: **signal on the board, detail in the tab.**
Flagged rows get a coloured badge in the `#` cell and a severity spine, so you
can see where the problems are while scrolling; the drawer holds the reasoning.

---

## 5. What a run does

1. `POST /api/validate {mode}` → starts a background job (same machinery
   renders use), returns a job id. Survives a closed tab; appears in Logs.
2. Passes run in order; `progress` updates the job stage as it goes.
3. Report is written to `<project>/validation.json` and returned by
   `GET /api/validation`.
4. The board paints badges from that report **after load** — so a run updates
   the board in place, with no reload.
5. `refreshUsage()` fires immediately so the new spend shows at once.

Modes: `rules` (free, no key), `text` (1+2), `full` (1+2+3, default).

**A failed pass is an error, never a clean board.** If a batch is refused, a
key is missing, or a cap is hit, the report keeps the findings gathered so far
but sets `status: "error"`, the job goes red, and the drawer says in words that
the rows it never reached are unchecked rather than clean.

---

## 6. Cost and safety

Every call goes through `usage.gate("claude", 1, model=…)`. There is no path in
`validator.py` that reaches the API without it.

- Same daily-spend cap as Gemini and TTS (`MAX_DAILY_SPEND_USD`).
- Per-job and daily call caps (`MAX_CLAUDE_CALLS_PER_JOB` 400,
  `MAX_DAILY_CLAUDE_CALLS` 1200).
- Metered from the tokens the API actually reports, not a flat guess.
- An unknown Claude model is billed at the **Opus** rate, so spend is never
  understated.
- Shown in the board header as `N gemini · N claude · N tts · $X`, today and
  all-time. The tooltip distinguishes Gemini's placeholder rates from Claude's
  published list rates.

Key: `CLAUDE_API_KEY` (Railway), falling back to `ANTHROPIC_API_KEY`. Passed
explicitly — the SDK's own env lookup only knows the second name.

---

## 7. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `VALIDATOR_MODEL` | `claude-opus-5` | Opus is the default *because* this is the accuracy backstop |
| `VALIDATOR_EFFORT` | `medium` | pass 1 already narrowed the question; raise for a pre-publish sweep |
| `VALIDATOR_BATCH_ROWS` | `25` | rows per text call |
| `VALIDATOR_MAX_VISION_ROWS` | `24` | hard ceiling on image calls per run |
| `VALIDATOR_VISION_MAX_PX` | `1200` | longest edge sent to the vision pass |
| `VALIDATOR_FLASH_SEC` | `1.0` | below this a panel reads as a flicker |
| `VALIDATOR_LONG_HOLD_SEC` | `12.0` | silent hold at or above this is dead air |

---

## 8. Room for improvement

Ordered by how much they matter.

1. **The blind spot: a confidently wrong description.** Pass 2 trusts the
   description; pass 3 only opens images for rows pass 2 already flagged. So a
   description that is wrong *and* plausible produces a wrong match that nothing
   flags and no image ever checks. **Fix:** spot-check a random sample of
   unflagged rows with vision each run — a handful of calls buys a real estimate
   of what the chain is missing.
2. **Batch-boundary blindness.** Pass 2 sees 25 rows; if the panel a line
   belongs to sits in the next batch, the mismatch is invisible. **Fix:**
   overlap batches by a few rows, or precede them with one cheap
   chapter-level summary pass.
3. **No feedback loop.** It never learns from the corrections actually made.
   **Fix:** record which findings were acted on vs. dismissed, and feed
   confirmed examples back as few-shot cases. This is the single biggest
   accuracy lever available.
4. **Findings are advisory only.** The fix still has to be done by hand, even
   when the suggestion is precise ("move to row 3"). **Fix:** wire common
   suggestions to the board actions that already exist — untick, move segment.
5. **No incremental re-run.** Fixing three rows re-validates all 138. **Fix:**
   hash each row, re-check only what changed, carry the rest forward.
6. **Precision is unmeasured for the Claude passes.** The rule pass was
   verified by hand at 5/5 true positives; the Claude passes have no measured
   false-positive rate. Until they do, treat findings as leads.
7. **The credit-page rule is a heuristic** (2+ markers) and could misfire on a
   panel whose dialogue mentions a website.
8. **The vision cap is arbitrary.** At 40 flagged rows, 16 go unconfirmed.
   Better would be to rank by severity × confidence and spend the budget on the
   most doubtful.
9. **It validates the board, not the video.** Render artefacts, audio sync, and
   anything that only exists after the export are out of scope.

---

## 9. Pros and cons

**Pros**

- Targets the exact failure the owner was fixing by hand, rather than checking
  what is easy to check.
- The free tier always runs and needs no key, so every project gets some
  checking regardless of budget.
- Ordered so the expensive pass is last and smallest; deterministic work is
  never paid for.
- Cost is capped, metered from real tokens, and visible on the page.
- Fails loudly. It will report "unchecked" but never a false "clean".
- Non-destructive — it annotates, never mutates the board.
- Cleared findings stay visible, so the third pass is auditable.

**Cons**

- Costs real money per chapter in the Claude modes, and the vision pass is the
  expensive part.
- Adds a step and up to a minute of latency before review.
- False positives are possible and directly erode trust in the tool; the design
  leans hard on precision to compensate, which means it will also miss things.
- Has a structural blind spot (item 1 above) that the current design cannot see
  into.
- Another moving part: the prompts encode assumptions about the pipeline, and
  will drift as the pipeline changes.
- As of this writing, the Claude passes have never run against the live API —
  they are covered by 54 stub-driven assertions only.

---

## 10. First real run

Use `text` mode on one chapter, with the cost header visible. Compare its
findings against your own read of the same chapter before trusting `full` mode
or wiring it into any automated step.
