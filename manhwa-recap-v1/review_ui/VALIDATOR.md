# The Check tab — how the validator works

Reference for `validator.py`, `validator_actions.py`, the routes in
`server.py`, and the Check drawer in `storyboard.py`. Kept in the repo
deliberately: the last handbook lived in a scratchpad and rotted within a week.

---

## 1. Why it exists

The board is assembled by five machines in a row — splitter, OCR, vision
describer, matcher, segmenter — and none of them is 100% accurate. The only
thing between a wrong row and a published video was the owner reading all 138
rows by hand, every chapter.

The matcher is the weak link, and it fails in a specific way. It matches on
embedding similarity, so it does not put a line on a *random* panel — it puts
it on a panel that is **topically related but wrong**. Those survive a tired
skim, because nothing about the row looks broken.

**The first version of this checker was too local.** It asked, of each row in
isolation, "does this line plausibly fit this panel?" That question cannot see
the failures that actually hurt a recap, because those are failures of
*sequence*: a reveal shown before its setup, a reaction before its cause, a
line that fits the panel it landed on but belonged four rows earlier. A
row-local checker reads every one of those as fine.

So the checker now reads the chapter first, and judges every row against its
place in that chapter.

---

## 2. What it checks

The five board columns, as data rather than as rendered HTML — a finding has to
point at something fixable, not at a formatting artefact.

| Column | Source | What goes wrong |
|---|---|---|
| Panel | `crops/<panel_id>.png` | flash panels; panels never shown |
| System OCR | `descriptions.json` → `ocr_text` | scanlation credits read as story text |
| System description | `descriptions.json` → `visual_description` | empty, generic, or describing a different panel |
| Script placement | `script.json` | **line on the wrong panel**, order jumps, scene breaks, units with no panel |
| On-screen timing | `segments.json` | sub-second flashes, dead air, stalls on one image |

---

## 3. The chain

```
rules → A: chapter map → B: sequence → D: description/OCR → E: vision → owner
free    one call          overlapping   batched             flagged +
                          windows                           spot checks
```

Each pass removes work from the next, so the expensive pass runs last and
smallest.

### Rules — free, deterministic, always runs

Anything with an exact answer is computed rather than paid for.

- **Flash panel** — in-video panel on screen under `FLASH_SEC` (1.0s).
- **Dead air** — silent panel held `LONG_SILENT_HOLD_SEC` (12s) or more.
- **Stall** — two or more segments stacked on ONE panel for
  `SAME_PANEL_RUN_SEC` (15s) together. *(Checked per row via the row's segment
  count — an earlier version walked for consecutive rows sharing a panel, which
  can never happen, because `build_rows` emits exactly one row per panel. It
  silently never fired.)*
- **Story-order inversion** — a panel carrying an earlier unit than the one
  above it.
- **Credit page carrying narration** — OCR with 2+ scanlation markers. A credit
  page already left out is the pipeline working, and is *not* reported.
- **Missing / failed / generic description** — nothing real for the matcher to
  have matched on.
- **Coverage** — a narration unit with no panel in the final video.

> **A check deliberately absent.** "Can the narration be said in the time
> available" is not checkable, because it cannot fail: the timeline is *derived
> from* the TTS audio, so words always fit by construction. An earlier version
> checked words-per-second and raised 27 false alarms on a clean chapter,
> because folded panels each carry a *copy* of their shared beat's full text.
> Removed; frozen as a regression test.

### Pass A — Chapter map (one call)

Reads the narration **in order** and returns scene boundaries, each scene's arc
phase (setup / escalation / climax / resolution), where the reveals land, the
emotional turns, and chronology notes. Everything downstream is judged against
this, and it travels as a cacheable prefix so it is paid for once.

The map is stored in the report and shown in the drawer. That is not
decoration: if the scene map is wrong, every sequence finding built on it is
suspect, and this is the only place that is visible.

### Pass B — Sequence validation (overlapping windows)

Windows of rows judged in order against the map. Reports four categories:

- `placement` — the line does not belong to this panel. When a panel in the
  window is plainly the right one, it names that row, which is what turns
  "wrong panel" into a one-click swap.
- `order` — the line lands too early or late; includes reveal-before-setup and
  reaction-before-cause.
- `continuity` — the panel belongs to a different scene than its neighbours.
- `pacing` — the rhythm hurts the chapter at this point.

**Windows overlap** (`SEQ_OVERLAP`, 5 of 25 rows). This is not redundancy: a
mismatch is recognised by seeing the row the line belongs to, so any mismatch
whose halves fall either side of a window edge is invisible without it.
Findings carry a stable id, so a row seen by two windows is reported once.

### Pass D — Description / OCR sanity

The pass aimed at the premise everything else stands on. Each row is shown with
its neighbours' descriptions, and the pass hunts descriptions that contradict
their own OCR or break continuity with both neighbours, and OCR that is
watermark, bleed-through, or garble. Reports `description` and `ocr`.

### Pass E — Vision (flagged rows **and** a clean-row sample)

- Opens the real crop for flagged rows, capped at `MAX_VISION_ROWS` (24).
- **Also samples `SPOT_CHECK_ROWS` (6) rows nothing flagged**, stratified evenly
  across the chapter. This is the only defence against the chain's own blind
  spot: B and D reason from descriptions, so a description that is wrong *and
  plausible* yields a row that looks clean to both. The spot-check budget is
  reserved *before* flagged rows are queued, so a noisy chapter cannot crowd it
  out.
- A spot check that **confirms** becomes a finding in its own right — that is
  the whole reason the sample exists.
- Returns `confirmed` / `cleared` / `unclear`. A **cleared** finding is demoted
  and annotated, never deleted, so the pass stays visible when it does its best
  work.

---

## 4. Severity and category

They are two different facts and are kept separate.

**Severity** is *how sure we are the row is wrong* — not how much it matters:

| Value | Shown as |
|---|---|
| `high` | Definitely wrong |
| `medium` | Likely wrong |
| `low` | Worth review |

**Category** is *what kind of defect*: `placement`, `order`, `continuity`,
`pacing`, `description` (likely description error), `ocr` (likely OCR poison),
`coverage`. Every finding also carries a numeric `confidence`.

---

## 5. Actions — findings act on the real board

Every finding carries actions, chosen for its category. Server actions run
through **the same `storyboard_edit` functions the board's own controls use**,
inside the same undo snapshot — so a fix applied from Check is indistinguishable
from the same fix made by hand, lands in one undo step, and cannot drift from
board state.

| Action | What it does | Via |
|---|---|---|
| Go to row | scrolls and flashes the row | client |
| Open panel image | opens the crop | client |
| Swap to row N | moves the segment onto the panel the checker named | `assign_panel` |
| Move line earlier / later | reorders the timeline by one slot | `reorder` |
| Leave this panel out / Put back | unticks / re-ticks every segment on the panel | segments writer |
| Use full panel | replaces the crop with the whole panel | `use_full_panel` |
| Re-run description | re-runs the vision describer on one panel | `panel-describe` |
| Re-run OCR | re-reads OCR only, keeping the description | `panel-describe` |
| Send to review | marks the segment `pending` | `review.json` |
| Accept as intentional | records the judgement; stops it being re-raised | feedback store |

Notes on deliberate choices:

- **Swap does not re-sequence the story.** Fixing *which picture shows* and
  *where the line sits* are separate decisions, so they are separate actions.
- **Leave out unticks; it does not delete.** The row, narration and audio all
  stay, so it is one click back.
- **Send to review marks `pending`, not `rejected`.** The checker's job is to
  raise a question; answering it by removing the segment is the owner's call.
- **Re-OCR keeps the description.** Overwriting a good description with a fresh
  guess would be a regression the owner did not ask for.
- A board-changing action stamps the stored report **stale**, so the drawer says
  the findings predate the last fix instead of presenting them as current. Tick
  *"Re-run the check automatically after applying a fix"* to re-check instead.

---

## 6. The feedback loop

`validation_feedback.json` per project, keyed by a stable finding id derived
from `panel_id + category` — deliberately not from the wording, which is
model-written and changes every run.

- **accepted** / **fixed** — the finding returns **demoted and marked**, never
  dropped (a finding that vanishes is one the owner can never reconsider, and
  the board would look cleaner than it is). Those rows are also named to the
  sequence pass so it stops raising them.
- **dismissed** — recorded distinctly, because "I fixed it" and "I disagree" are
  different signals and averaging them would lose the only evidence of which
  findings were worth having.
- **reopened** — clears the judgement.

---

## 7. Why it is its own tab

1. **It is a mode, not a row action** — it operates on the whole board, like
   Ingest or Tracker.
2. **Findings are a list and need room** — issue, fix, provenance, and a row of
   action buttons do not fit in a table cell.
3. **It costs money, so it needs a deliberate surface** with a mode picker and a
   confirm. An ambient check would spend without being asked.
4. **It is advisory, and should look advisory** — findings written into the
   board would read as the system having changed your work.

The compromise is two levels: **signal on the board, detail in the tab.**
Flagged rows get a badge and a severity spine; the drawer holds the reasoning
and the buttons; jumping from a finding scrolls *and flashes* the row.

---

## 8. Sidebar order

**Ingest → Board → Check → Exports → Review → Projects → Tracker → Logs**

The order encodes how a chapter actually moves: ingest it, build it, check it,
export it, review it — then the standing tools. Identical on the board and
`/review`, and asserted as a sequence in `test_review.py` (a presence check
would pass on any shuffle).

---

## 9. Cost and safety

Every Claude call goes through `usage.gate("claude", 1, model=…)`. There is no
path in `validator.py` that reaches the API without it. Re-describe and re-OCR
go through the Gemini gate the describe stage already uses.

- Same daily-spend cap as Gemini and TTS; plus per-job and daily call caps.
- Metered from the tokens the API actually reports.
- An unknown Claude model is billed at the **Opus** rate, so spend is never
  understated.
- Shown in the header as `N gemini · N claude · N tts · $X`, today and all-time.

Key: `CLAUDE_API_KEY` (Railway), falling back to `ANTHROPIC_API_KEY`. Passed
explicitly — the SDK's own env lookup only knows the second name.

| Setting | Default |
|---|---|
| `VALIDATOR_MODEL` | `claude-opus-5` |
| `VALIDATOR_EFFORT` | `medium` |
| `VALIDATOR_BATCH_ROWS` | `25` |
| `VALIDATOR_SEQ_OVERLAP` | `5` |
| `VALIDATOR_MAX_VISION_ROWS` | `24` |
| `VALIDATOR_SPOT_CHECK_ROWS` | `6` |
| `VALIDATOR_VISION_MAX_PX` | `1200` |
| `VALIDATOR_FLASH_SEC` | `1.0` |
| `VALIDATOR_LONG_HOLD_SEC` | `12.0` |
| `VALIDATOR_SAME_PANEL_RUN_SEC` | `15.0` |

---

## 10. Remaining limitations

1. **Spot checks narrow the blind spot; they do not close it.** Six sampled rows
   out of 138 is a probe, not a guarantee. Raise `VALIDATOR_SPOT_CHECK_ROWS` to
   trade cost for coverage.
2. **The chapter map is a single point of failure.** Every sequence finding is
   judged against it, so a bad map degrades the whole pass. It is shown in the
   drawer so it can be sanity-checked, but nothing validates it automatically.
3. **The feedback loop suppresses, it does not teach.** Accepted findings stop
   being raised, but confirmed ones are not yet fed back as few-shot examples —
   the bigger accuracy lever, still unbuilt.
4. **No incremental re-run.** Fixing three rows re-validates all 138.
5. **Claude-pass precision is unmeasured.** The rule pass was verified by hand
   (true positives on a real chapter); the Claude passes have no measured
   false-positive rate. Treat findings as leads.
6. **The credit-page rule is a heuristic** and could misfire on a panel whose
   dialogue mentions a website.
7. **Actions are single-step.** There is no "apply all" or batch fix, by
   design — but a chapter with twenty placement errors is twenty clicks.
8. **It validates the board, not the video.** Render artefacts and audio sync
   are out of scope.
