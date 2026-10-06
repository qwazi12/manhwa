# Thumbnail generation recipe — Flamingo Remix
## A reusable, per-chapter spec for the server-side compositor

Written 2026-10-06. Derived from: 8 top-performing thumbnails analysed across Manhwa Recap Zone / Mamoru Manhwa / Recap Manhwa, plus a diagnosis of the 3 current outputs you attached.

**Architecture note.** Your system composites from the chapter's own panels — no AI generation, no cost. So this is written as a **deterministic compositor recipe**: fixed geometry, fixed tokens, fixed order. Every parameter is a blank the pipeline fills per chapter. Part 6 gives the AI-generation variant for the cases where a panel is unusable.

---

# PART 0 — Diagnosis: what the three current outputs are doing wrong

Analysed from your three samples (all `CH 358`, *I Am the Fated Villain*).

| Symptom in your output | Root cause | Fix (§) |
|---|---|---|
| Vertical cover pillarboxed with blurred left/right bars | Cover pasted to fit, not cropped to fill | §3B |
| No face large enough to read | No face-first panel selection; cover chosen even when the face is small | §5 |
| Series logo text at the bottom (`I AM THE FATED VILLAIN`) | The **cover's own typography** is being carried into the thumbnail | §4.1 — never composite over the cover's logo; it is unreadable at feed size and it isn't a hook |
| `I AM THE FATED` **completely unreadable** at feed size | Text inherits the cover's decorative font at small scale | §4.1 — minimum cap height, condensed heavy sans only |
| `CH 358` badge legible but tiny; phone-shot version illegible | Badge sized as decoration, not as an element | §4.3 |
| **No arrow** | Not implemented | §4.2 |
| Speech bubble from the raw panel showing through, overlapped by title text | Panel used uncropped, no bubble suppression at composite stage | §5.4 |
| No hook words on the two "panel" designs | Hook text not applied to those compositions | §3 |

**The single biggest miss:** none of your outputs has a **large protagonist face**. Across the 8 top performers, 8/8 use cropped panel art with a prominent face; 6/8 add a yellow arrow at it; 8/8 use bold yellow text with a thick black outline. Your outputs use a *cover*, which is a poster — designed to be read at full size, and identical across every chapter of a series.

---

# PART 1 — Canvas and design tokens

Fixed. Never varies per chapter.

```
CANVAS
  width            1280
  height           720
  aspect           16:9
  safe_margin      28          # nothing important outside this
  format           JPEG q=92, <2 MB

COLOUR TOKENS (sample accent from cover art, but keep these roles fixed)
  fill_yellow      #FFD400      # the niche standard — 8/8 top performers
  fill_yellow_alt  #FFC300      # use if accent clashes
  stroke_black     #000000
  shadow           #000000 @ 55% opacity, offset (0,4), blur 8
  glow             accent colour @ 70%, blur 24, behind the subject
  badge_bg         accent colour (from cover)
  badge_text       #111111 or #FFFFFF (whichever contrasts)

TYPOGRAPHY (condensed heavy sans only — Anton, Bebas Neue, Oswald Bold, Archivo Black)
  Never a decorative or serif face. Never the series' logo font.
  Letter-spacing 1-2%  ·  ALL CAPS  ·  optional slight italic
```

### Text size floor — the rule that fixes your unreadable text

Cap height on the 1280-wide canvas, by word count:

| Words | Cap height | Stroke | Notes |
|---|---|---|---|
| 1 | 180 px | 14 px | `GENIUS`, `UNLOCKED` |
| 2 | 132 px | 12 px | `HE WON` |
| 3 | 108 px | 11 px | `VILLAIN #1` |
| 4 | 90 px | 10 px | `THE FATED VILLAIN` |
| **Absolute floor** | **84 px** | **9 px** | below this, cut a word instead |

**Why:** a feed renders your thumbnail at roughly 160–250 px wide. An 84 px cap height survives that downscale; the ~24 px effective height of your current bottom text does not. **If the hook doesn't fit at the floor, drop words — never shrink.**

Stroke = `0.08 × cap height`, minimum 9 px, always pure black, always **outside** the glyph. Add the drop shadow after the stroke.

---

# PART 2 — The blanks to fill each chapter

```
{{SERIES}}         "I Am the Fated Villain"
{{CHAPTER}}        362
{{HOOK}}           1-4 words, uppercase        e.g. "HE WON"
{{ACCENT}}         hex sampled from cover art  e.g. "#C0392B"
{{PANEL_HERO}}     path to the best-scoring panel (§5)
{{PANEL_ALT}}      path to the second-best panel
{{COVER}}          path to the series cover
{{FACE_BOX}}       x,y,w,h of the MC face inside PANEL_HERO
{{TITLE_HOOK}}     a few words pulled from the video title
{{BADGE_TEXT}}     "CH {{CHAPTER}}"
```

Everything else in this document is constant. **The compositor takes these ten values and produces the file. No per-chapter design decisions.**

---

# PART 3 — Composition recipes

Six layouts. **A is the default.** Each press of ↻ rotates to the next, and always draws from panels not previously used.

## 3A — Panel hero (DEFAULT — this is the one you're missing)

```
1. Take PANEL_HERO. Crop to 16:9 using FACE_BOX as the anchor:
   the face must sit in the right 45% of frame, vertically centred in the
   upper 60%. Crop — NEVER pillarbox. If the panel is portrait, crop the
   sides out; do not add bars.
2. Upscale to 1280x720 (Lanczos).
3. If the crop is soft, apply unsharp mask r=2, amount=80%.
4. Place glow (#ACCENT @70%, blur 24) behind the face.
5. Text block: HOOK, bottom-LEFT, occupying the left 55% max.
6. Arrow: from the text block toward FACE_BOX centre.
7. Badge: top-left.
8. Optional: brighten the face region +8% exposure, and desaturate the
   background -20% so the MC separates from it.
```

## 3B — Cover hero, CROPPED (replaces your pillarbox version)

```
1. Take COVER.
2. Crop to fill 1280x720 — do NOT pillarbox, do NOT letterbox.
   Vertical cover at 720x1080 -> crop a 720x405 band.
   Band position: bias to the TOP-THIRD (y = 12% of cover height) where
   the character's face usually sits. Then refine using face detection
   on the cover: pick the band containing the largest face.
3. Upscale to 1280x720.
4. If upscale factor >1.9x, composite a blurred cover copy as background
   and place the sharp crop on the RIGHT 62%, feathered 40px — this is
   the ONLY permitted use of a background fill, and the sharp art must
   cover at least 60% of the frame.
5. Text block: HOOK, bottom-LEFT over a soft dark gradient
   (transparent -> #000 @65% across the bottom 38%).
6. Arrow toward the face.
7. Badge top-left.
```

## 3C — Split: panel + cover
`PANEL_HERO` fills the left 58% (cropped, face-anchored) · `COVER` cropped fills the right 42% · 4 px `#000` divider · HOOK bottom-left · arrow into the panel face · badge top-left.

## 3D — Two panels
Two panels 50/50 with a 4 px divider. The **left** panel carries the arrow. HOOK bottom-centre-left. Use when the chapter has a strong before/after beat.

## 3E — Cover inset over panel
`PANEL_HERO` fills the frame (face-anchored, cropped). `COVER` scaled to 26% height, placed bottom-right with a 6 px `#FFD400` border and a drop shadow. HOOK bottom-left. Badge top-left.

## 3F — Clean, no text
Panel hero, cropped, face-anchored, no text, no badge. **Only offer this when the face is unmistakably the MC and the panel is ≥90th percentile on composition score.** Used as an A/B half against 3A.

### Layout geometry (all recipes)

```
ZONES (never overlap)
  text zone     x 28..672   y 470..692      (left 50%, bottom band)
  face zone     x 700..1252 y 40..560       (right 43%, upper)
  badge zone    x 28..232   y 28..88        (top-left)
  inset zone    x 900..1252 y 470..692      (only 3E)

RULE: the text block and the face never intersect. If the crop puts the
face left, MIRROR the whole composition rather than moving the text.
```

---

# PART 4 — Element specifications

## 4.1 Hook text
- 1–4 words, ALL CAPS, from `{{HOOK}}`.
- Fill `#FFD400`, stroke `#000000` at the widths in §1, drop shadow.
- Position: bottom-left for 3A/3B/3D/3E; bottom-centre for 3C.
- **Never place text over the face. Grep your own output for this before posting.**
- **Never use the series logo or any decorative face.**
- If HOOK is longer than 4 words, **cut it** — do not shrink below 84 px.
- Wrap to at most 2 lines; line gap 0.92× cap height.

## 4.2 Arrow (the missing element — 6/8 top performers have one)
```
shape      straight or gentle-arc, pointing tip-to-face
fill       #FFD400
outline    #000000, 6 px, drawn OUTSIDE the shaft
shaft      16 px thick (taper slightly toward the tip)
length     200-340 px
head       56 x 56 px triangle
start      from the outer edge of the text block, or from a corner
end        18-30 px short of the face — NEVER touching it
```
One arrow only. It points at the MC, never at text, never at a background element.

## 4.3 Chapter badge
```
shape      rounded rect, radius 8 px
bg         {{ACCENT}}
text       "CH {{CHAPTER}}", bold condensed, 38 px cap height
padding    20 px horizontal, 12 px vertical
position   top-left at (28, 28)   ->  badge is ~150 x 62
outline    3 px #000000, plus drop shadow
```
Legible but unmistakably secondary. **It must not be the largest element on the canvas** — which is the failure mode of a pure "chapter number" thumbnail.

For the `362`-style short form: same geometry, text `362`, no `CH`, ~92 × 62.

## 4.4 Glow
Behind the MC only. `{{ACCENT}}` at 70% opacity, Gaussian blur 24, drawn before the subject so it rims them. Skip it if the panel's own lighting is already dramatic — a glow over an already-lit panel reads as amateur.

## 4.5 Gradients
A bottom gradient (`transparent → #000 @65%`, across the bottom 38%) is required **only** in 3B and 3C where text sits over the art. In 3A place text on a flat/simple region of the panel instead — a gradient over a busy panel kills the art.

---

# PART 5 — Panel selection: face-first

Replace the current "score by MC-in-cast + widescreen" pass with this order. **Selection is the whole game** — a perfect layout on a bad panel still fails.

```
STEP 1  Hard filters (skip entirely)
  - credit / title pages
  - near-black frames (mean luminance < 18)
  - speech-bubble coverage > 22% of the panel
  - watermark region detected
  - perceptual duplicate of an already-used panel
    (keep your existing dhash pass — extend it to catch near-dupes, not exact)

STEP 2  Face detection
  - run a face detector over each surviving panel
  - reject panels with no face >= 8% of panel area

STEP 3  Score each surviving panel
  face_area_pct          x 3.0     # biggest single driver
  is_mc                  x 2.5     # matched against the series cast list
  face_height_px         x 0.02
  contrast (stdev)       x 1.2
  sharpness (lap var)    x 0.02
  negative_space_score   x 1.5     # room for the text block
  - crowded / many faces x -1.0

STEP 4  Pick top 2
  PANEL_HERO = highest score
  PANEL_ALT  = highest score that is a perceptual non-duplicate of HERO

STEP 5  Validate for text
  Does HERO have a low-detail region >= 44% x 30% of frame for the text
  block? If not, UPGRADE the composition to 3B/3C/3E (which overlay a
  gradient) rather than shrinking the text.
```

**Reference crop rule — where to put the face:**

| Face position in the crop | Verdict |
|---|---|
| right 45%, upper 60% | ✅ target — text goes left |
| left 45%, upper 60% | ✅ mirror the composition |
| centre | ⚠️ acceptable only for 3D/3F |
| below 65% of frame height | ❌ recrop — the badge/text zone will fight it |
| smaller than 22% of frame height | ❌ reject the panel, go to the next |

**Bubble suppression at composite stage:** if STEP 1 let a small bubble through and the text block would overlap it, inpaint that rectangle with a median blur of its own surroundings before drawing text. Never let a bubble and your hook share pixels — that is what makes your current CH 358 output look broken.

---

# PART 6 — AI-generation prompt (for panels that fail §5)

Use only when no panel clears STEP 3 — a rare fallback, since it costs money where the compositor costs nothing.

```
Create a 16:9 YouTube thumbnail (1280x720) for a manhwa recap video.

SUBJECT: {CHARACTER_NAME} from {SERIES} — {PHYSICAL_DESCRIPTION: hair,
eyes, outfit, distinguishing marks}. Cropped panel framing: head and
shoulders, face large and dominant, occupying roughly the right 45% of
the frame, positioned in the upper 60%. Preserve the character's exact
canonical design — the same costume, hair and eye colour as the source.

EXPRESSION: {EMOTION — e.g. cold smirk / furious / calm menace}, looking
toward the viewer.

BACKGROUND: {SETTING}, rendered softer and darker than the subject so the
character separates cleanly. Add a {ACCENT_COLOUR} rim-light glow behind
the character.

TEXT: the words "{HOOK}" in the bottom-left, set in a condensed heavy
sans-serif (Anton or Bebas Neue), ALL CAPS, in bright yellow #FFD400 with
a thick black outline and a soft black drop shadow. The text must be
crisp, correctly spelled, and not overlap the character's face.

Also draw one thick yellow arrow with a black outline, pointing from the
left toward the character's face, ending short of it.

Top-left: a small rounded rectangular badge in {ACCENT_COLOUR} reading
"{BADGE_TEXT}" in bold black text.

Over the bottom third, a subtle gradient from transparent to 65% black so
the text stays readable.

Style: polished manhwa/webtoon illustration, high contrast, dramatic
lighting, sharp linework. No logos, no watermarks, no site text, no
speech bubbles, no additional characters.
```

**Then composite the text, arrow and badge yourself** rather than trusting the generator to spell them — pass the art through the same §4 compositor so every chapter matches exactly. Only the art comes from the model.

---

# PART 7 — Worked example

**Chapter 362, `HOOK = "HE WON"`, accent `#C0392B` (guessed from the cover — replace with the sampled value).**

```
Recipe         3A panel hero
Panel          best-scoring panel with Gu Changge's face, cropped to 16:9,
               face anchored right 45% / upper 60%
Background     darkened -20%, desaturated, accent glow behind the head
Text           "HE WON"  ·  2 words  ·  cap 132 px  ·  stroke 12 px  ·  #FFD400
               bottom-left, block x 28..560, baseline y ~640
Arrow          from (600, 560) to (890, 330), 16 px shaft, 6 px black outline,
               tip 24 px short of the face
Badge          "CH 362"  ·  top-left (28,28)  ·  #C0392B bg  ·  38 px bold black text
Gradient       none (3A)
Output         1280x720 JPEG q92
```

**Rotations on ↻:** 3B (cover cropped, no pillarbox) → 3C (panel+cover split) → 3E (cover inset) → 3D (two panels) → 3F (clean) → 3A. Each pass uses unseen panels and shifts the accent hue ±12°.

**Same for the range video** (every ~20–30 chapters): identical recipe and identical frame, different panel and different words. `HE TOOK EVERYTHING` for the range, `HE WON` for the chapter. Consistency in form, variation in substance.

---

# PART 8 — Machine-readable recipe

```json
{
  "canvas": {"w": 1280, "h": 720, "margin": 28, "format": "jpeg", "quality": 92},
  "tokens": {
    "fill": "#FFD400", "stroke": "#000000", "shadow_opacity": 0.55,
    "glow_opacity": 0.70, "glow_blur": 24, "gradient_to": 0.65,
    "font": "Anton", "letter_spacing_pct": 1.5, "uppercase": true
  },
  "text": {
    "max_words": 4,
    "cap_height_by_words": {"1": 180, "2": 132, "3": 108, "4": 90},
    "cap_height_floor": 84,
    "stroke_pct_of_cap": 0.08,
    "stroke_min": 9,
    "max_lines": 2,
    "overlap_face": false
  },
  "arrow": {
    "fill": "#FFD400", "outline": "#000000", "outline_px": 6,
    "shaft_px": 16, "length": [200, 340], "head_px": 56,
    "gap_to_face_px": [18, 30], "count": 1, "target": "face"
  },
  "badge": {
    "radius": 8, "bg": "$ACCENT", "text_color": "auto",
    "text_cap_px": 38, "pad_x": 20, "pad_y": 12,
    "position": "top-left", "origin": [28, 28], "outline_px": 3,
    "template": "CH {{CHAPTER}}", "short_template": "{{CHAPTER}}"
  },
  "zones": {
    "text":  {"x": [28, 672],  "y": [470, 692]},
    "face":  {"x": [700, 1252], "y": [40, 560]},
    "badge": {"x": [28, 232],  "y": [28, 88]},
    "inset": {"x": [900, 1252], "y": [470, 692]}
  },
  "crop": {"mode": "fill", "anchor": "face", "allow_pillarbox": false},
  "panel_score": {
    "face_area_pct": 3.0, "is_mc": 2.5, "face_height_px": 0.02,
    "contrast": 1.2, "sharpness": 0.02, "negative_space": 1.5,
    "crowded_faces": -1.0
  },
  "panel_reject": {
    "mean_luminance_below": 18, "bubble_coverage_above": 0.22,
    "no_face_area_below": 0.08, "face_height_below_pct": 0.22,
    "duplicate": true
  },
  "recipes": ["3A_panel_hero", "3B_cover_cropped", "3C_split",
              "3E_cover_inset", "3D_two_panels", "3F_clean"],
  "default_recipe": "3A_panel_hero",
  "rotate_on_reroll": true,
  "cover_trim": {"top_pct": 7, "bottom_pct": 5},
  "output": {"w": 1280, "h": 720}
}
```

---

## The four changes that matter most

1. **Crop, never pillarbox.** The blurred side bars are the single most recognisable "auto-generated" tell in your current output.
2. **Never composite over the cover's logo.** It carries unreadable decorative type and it isn't a hook. Put your own 1–4 yellow words on it.
3. **Add the arrow.** Six of eight top performers have one and it is one line of drawing code. It is the cheapest large gain available to you.
4. **Set a text size floor of 84 px cap height and enforce it.** Your current bottom text is ~24 px effective — that is why `I AM THE FATED` failed the 120 px read.
