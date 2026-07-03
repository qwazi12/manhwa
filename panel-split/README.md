# Panel + Sub-Shot Extractor

Turns a manhwa/webtoon page image into usable **shots** for the recap
pipeline. Works in two layers:

1. **Primary panels** — cuts the page along gutters (white or black gaps)
   into panel regions. Handles stacked and side-by-side panels.
2. **Sub-shots inside a panel** — a tall continuous action panel (a long
   falling sequence, a drawn-out fight) is not one visual beat, it's
   several. Those get split into multiple sub-shots automatically.

The guiding idea: the renderer cares about **usable shots**, not perfect
panel boundaries. A tall panel with three moments of motion should become
three shots, not one static image.

## What comes out

- Crop images:
  - `pageNNN_panel_XXX.png` for a normal single panel
  - `pageNNN_panel_XXX_shot_YY.png` for each beat of a multi-beat panel
- `panels.json` — bounding boxes, panel type (`single` or
  `continuous_vertical_action`), and reading order for every panel and
  shot. This is compatible with the recap pipeline's `shots.json` idea, so
  a later stage can choose to use the full panel, a sub-shot, or pan across
  the whole panel.

## How the sub-shot decision works

For each primary panel:

- If height/width ratio is below `TALL_RATIO` (1.8) → **single shot**.
- If it's tall **and** has a clean internal gutter → cut on the gutter
  (handled by the normal panel logic).
- If it's tall with **no** internal gutter → **sliding-window sub-shots**:
  generate overlapping vertical windows, score each by content density
  (how much non-background art is in it), keep the strongest non-
  overlapping windows top-to-bottom.

If a tall panel has weak content variation and the windows all look
similar, you may prefer to keep it as one tall crop and let the renderer
pan across it — `panels.json` gives you the coordinates to do either.

## What it deliberately does NOT do (yet)

These were considered and left out on purpose — they're Stage 3 work, not
worth building until a real chapter test shows they're needed:

- Speech-bubble masking (contour detection)
- Face / person detection
- Saliency models

The content-density sliding window is the simple, dependency-light stand-in
for all of that. If validation shows it isn't good enough on real action
panels, that's the signal to add the heavier detection — not before.

## Blank crop archiving

After cropping, each crop is checked for content density. Anything mostly
background (a transition page, a stray empty gutter sliver, a near-blank
"meanwhile..." panel) is moved to `archive_blank/` inside the output
folder instead of sitting alongside the real panels — keeps the main
output clean and saves space if you're batch-processing full chapters.

Nothing is deleted by default; blanks are archived, not discarded, so you
can double check none of them were actually meaningful before clearing
that folder out. `panels.json` also marks each panel/shot with
`"blank": true/false` so you can filter programmatically too.

Disable this and keep everything in the main folder with:
```bash
python split_panels.py --input page.webp --out output --keep-blanks
```

Tuning: `BLANK_DENSITY_THRESHOLD` (default 0.015) controls how strict the
blank test is — raise it if genuinely sparse-but-meaningful panels (a
character alone in an empty room) are getting archived by mistake.

## Integration Workflow with manhwa-recap-v1

To split your raw screenshots, review the cuts, and load them into the video pipeline:

### Step 1: Split & Archive Blanks
Run the extractor on a folder of raw chapter screenshots. Direct the output to a temporary review folder (e.g. `review_crops`):
```bash
python split_panels.py --input path/to/raw_screenshots --out review_crops --batch
```
This automatically splits the pages and moves blank/transition panels into `review_crops/archive_blank/` to keep your workspace clean.

### Step 2: Manual Review
Open the `review_crops/` folder. Look through the crops and simply **delete** any bad cuts or unwanted images.

### Step 3: Rename & Import
Run the helper script to naturally sort the remaining crops, rename them sequentially (`001.png`, `002.png`, ...), and copy them directly to the recap input folder:
```bash
python import_crops.py --from-dir review_crops --to-dir ../manhwa-recap-v1/input/images
```

## Always spot-check

Automatic detection gets most panels right but will occasionally over-split
(a textured gutter) or under-split (a full-bleed panel with no gap). For a
dozen panels this review takes under a minute and catches problems before
they reach the video pipeline.

## Tuning

All thresholds are constants at the top of `split_panels.py`:

| Constant | Effect |
|---|---|
| `BG_COLOR_TOLERANCE` | How close a pixel counts as gutter. Raise for textured gutters; lower if art gets falsely split. |
| `GUTTER_FRACTION` | Fraction of a row/col that must be gutter-colored to cut there. Lower if gutters have stray marks. |
| `MIN_GUTTER_RUN` | Minimum gutter thickness before it counts as a real gap. |
| `MIN_PANEL_SIZE` | Smallest panel kept; smaller pieces are discarded. |
| `EDGE_MARGIN` | Pixels trimmed off each cut to avoid gutter bleed. |
| `TALL_RATIO` | Height/width above which a panel is considered for sub-shots. |
| `SHOT_WINDOW_MIN/MAX` | Size range of sub-shot windows (px). |
| `SHOT_OVERLAP` | Overlap between candidate windows. |
| `SHOT_SCORE_KEEP` | How strong a window must score (vs the best) to be kept. |
| `BLANK_DENSITY_THRESHOLD` | Content-density cutoff below which a crop is archived as blank. |
| `ARCHIVE_BLANKS` | Default on/off for blank archiving (override per-run with `--keep-blanks`). |
