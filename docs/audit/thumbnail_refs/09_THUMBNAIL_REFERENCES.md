# Thumbnail reference pack — for Claude Code

Read this alongside `07_THUMBNAIL_RECIPE.md` (the spec) and `08_PROMPT_ClaudeCode_thumbnail_and_publishing.md` (the work order). This file tells you what each reference image is and what to take from it.

**Where these go in the repo:** `docs/audit/thumbnail_refs/`.

---

## 1. `REF_geometry_sheet.png` — implement from THIS

The single most useful file for coding. It is a 1280×720 diagram of the exact geometry in spec §3:

- The three zones with their pixel bounds (badge `x28-232 y28-88`, face `x700-1252 y40-560`, text `x28-672 y470-692`)
- The cap-height table (1 word 180px · 2 words 132px · 3 words 108px · 4 words 90px · floor 84px)
- The arrow anatomy (16px shaft, 56px head, 6px black outline, tip stopping 18–30px short of the face)
- The stroke rule (black, 0.08 × cap height)
- The two hard rules: text never overlaps the face zone; below 84px you cut words, never shrink

**Use it as the source of truth for coordinates.** If this sheet and the prose spec ever disagree, this sheet was generated from the JSON block in `07_THUMBNAIL_RECIPE.md` §8 — that JSON is authoritative.

---

## 2. `REF_before_after.png` — the target vs what exists now

Top: the current auto-generated output for `CH 358` (from the pipeline as it stands).
Bottom: the spec applied — cropped panel, large MC face, big yellow hook, arrow, corner badge.

The four differences to reproduce:

| Before (current) | After (target) |
|---|---|
| Vertical cover pillarboxed with blurred side bars | Cropped to fill 16:9 — no bars |
| Series' own cover logo as the bottom text | Our own 1–4 yellow words |
| No arrow | One yellow arrow at the face |
| Small distant face | MC face large, right 45% |

---

## 3. `sample_3A_panel_hero.png` — the DEFAULT composition

A generated mockup of spec recipe **3A (panel hero)** — the layout that does not exist in the codebase yet and should become the default.

Layout: MC face large on the right, hook text bottom-left, yellow arrow to the face, `CH 362` badge top-left.

**Note:** this is a *generated mockup*, not a real chapter panel. It exists to show the composition and the visual weight of each element. The real pipeline composites an actual chapter crop into the same geometry. Do not try to match the artwork — match the **layout**.

---

## 4. `sample_3D_two_panel.png` — the before/after composition

A generated mockup of spec recipe **3D (two panels)** — weak → strong. Use it to check your two-panel divider, arrow placement on the left panel, and dual-label handling (`SLAVE` / `MONARCH`).

---

## 5. `REF_competitor_grid.png` — the real thing (internal reference ONLY)

Eight real thumbnails from the highest-performing channels in this niche (Manhwa Recap Zone, Mamoru Manhwa, Manhwa Fresh, Daily Manhwa Fresh, Cokie Manhwa). This is the evidence base for the spec.

**What to take from it** — these are the patterns to reproduce:

| Pattern | Frequency | What it looks like |
|---|---|---|
| Bold **yellow** text with thick black outline | 8/8 | The niche standard. `#FFD400` + black stroke |
| 1–5 words only | 8/8 | Never a sentence, never a chapter number as the main element |
| Text in a **corner** | 8/8 | Usually bottom-left or bottom-right |
| Cropped panel art with a **large face** | 8/8 | Faces read at 120px |
| **Yellow arrow** pointing at the MC | 6/8 | Straight, thick, black-outlined |
| Glow / aura on the MC | 6/8 | Accent colour behind the subject |
| Status / chapter badge | 3/8 | Small corner pill — `#COMPLETED`, `NEW`, `1-8` |

**Use this grid for layout and weight only.** Do NOT copy competitor artwork, titles, or text into our output — the spec forbids it and YouTube's reused-content and misleading-metadata policies both apply.

---

## IP WARNING — read before committing any of this

`REF_competitor_grid.png` and the individual `ref_*.jpg` files contain **third-party artwork and other channels' designs**. They are for internal visual comparison only.

- ✅ Put them in `docs/audit/thumbnail_refs/` on your local machine
- ❌ Do NOT commit them to the public repo
- ❌ Do NOT feed them into any generated output
- ❌ Do NOT ship them to the server

This matters because the repo already has a known issue with scraped art in its history. Do not add to it. If Claude Code needs them tracked, add the folder to `.gitignore` and note it in the commit message.

The three generated mockups (`sample_3A`, `sample_3D`, `REF_geometry_sheet`) are safe to commit — the geometry sheet contains no third-party art at all, and the two mockups are generated illustrations.

---

## What to tell Claude Code about these files

```
docs/audit/thumbnail_refs/ contains five reference images. Read them.

REF_geometry_sheet.png      -> the exact geometry. Implement COORDINATES from this.
                               The JSON in 07_THUMBNAIL_RECIPE.md section 8 is authoritative
                               if anything disagrees.
REF_before_after.png        -> top = current output, bottom = the target. Close this gap.
sample_3A_panel_hero.png    -> the DEFAULT composition to build (spec route 3A).
sample_3D_two_panel.png     -> the two-panel composition (spec route 3D).
REF_competitor_grid.png     -> 8 real top-performing thumbnails. Use ONLY to judge layout
                               and visual weight. Never copy this art, text or design.
                               It is internal reference - never commit it, never ship it.

Match the GEOMETRY and the ELEMENT WEIGHT. Do not try to match the generated artwork.
```
