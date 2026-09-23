# Regression fixture — Murim Psychopath ch.43

20 page images, pinned 2026-09-23. **Never re-scrape for a regression run.**
A gate must compare CODE against CODE; re-scraping compares code-plus-source
and makes a failure unattributable.

## Baseline

**151 panels** — pinned images, current splitter, **3 runs, variance ±0**.

Determinism proven: three consecutive runs produced 151 crops with identical
filenames and byte-identical images. The splitter has no threading,
inference or iteration-order variance on this input.

## Why the old "156" was retired

It was never reproducible and was gating decisions it could not support:

| config, on THESE pinned images | panels |
|---|---|
| pre-session code (quietest-row fallback) | 162 |
| current code (border-only cuts + readable ceiling) | **151** |
| legacy recorded "baseline" | 156 (not reproducible) |

A fresh scrape is **byte-identical** to the pinned images (20/20 files, same
sha256), so the source did not drift — the earlier "~3% source variance"
reading was wrong. The 162 → 151 movement is entirely attributable to
splitter changes made during the 2026-09-22 session. 156 matches neither and
its provenance is unknown; it is retired rather than reconciled.

## Consequence for past gates

The Stage 4 gate compared a candidate splitter (125) against 156 — a number
produced by different code. That comparison conflated regression with
intentional change. Re-run against 151.


## Baseline re-recorded: 125 (2026-09-23)

Arbitrated by classifying every cut the two configs disagree on, rather than
comparing counts — counts cannot tell "new config misses real cuts" from
"old config was over-splitting", and this project has been misled by raw
counts twice.

**161 cuts present in the production config and absent from the new one:**

| classification | count |
|---|---|
| (b) interior feature — bubble band, frame stroke, SFX, tonal transition | **143** |
| (a) real gutter — a full-width flat band the new config skips | **18** |

Verdict **(b)**: the production splitter was shredding panel interiors, and
the 151 baseline encoded that. Baseline re-recorded at **125**.

**Known cost, not hidden:** the new config misses **18 genuine gutters** on
this chapter. It is better on net (143 bad cuts avoided vs 18 good ones lost)
but it is not strictly better.

### A classifier bug caught mid-arbitration
The first pass classified a cut as a gutter only if the band matched the
page's single registered background. That labelled **white bands on a
black-background page** as "interior features" — rows with a row-std of 0.55
and 2.91, i.e. essentially perfect flatness, rejected for being the wrong
colour. It is the same single-background hole already fixed in flat-band
detection. Re-run with the correct rule (a gutter is a FLAT row whatever its
colour, verified flat both across width and down the band), the tally moved
from 8/153 to 18/143.

Evidence: `cutmaps/` (the 3 pages with the most disagreement; red = prod cuts,
green = new cuts, yellow = a differing cut measured as a real gutter),
and `cut_arbitration.json` in the session scratchpad.

## The gate cannot validate this baseline — the cut maps do

Because the baseline was re-recorded **as the new config's own output**, the
regression gate now compares that config against itself: 125 vs 125, 0.0%,
inside any band. **It cannot fail, and it is not evidence of correctness.**

Its only value is as a FUTURE consistency check — "does this config still
produce 125 after the next change?"

The evidentiary basis for the splitter's correctness is the **cut-map
classification** (143 interior features vs 18 real gutters, `cutmaps/`),
which is independent of the gate. A future session must not cite
"the gate passes" as validation.
