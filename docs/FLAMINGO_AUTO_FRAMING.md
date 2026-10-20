# Automatic orientation and subject framing

Status: **Ready for Review** — measured on reviewed Chapter 34 decisions (2026-10-20).
Code: `manhwa-recap-v1/auto_framing.py`, wired into `shot_planner.plan_shots` (new
ingests) and `storyboard_edit.auto_frame` / `POST /api/storyboard/auto_framing`
(existing chapters, the board's **Auto-frame** button). Tests:
`review_ui/test_auto_framing.py` (27, no network).

## What it does

Two vision questions per panel, cached by image content
(`<project>/auto_framing_cache.json`, so a re-plan never pays twice):

1. **Subject** (every panel a shot uses): the box of the main subject, excluding
   speech/thought balloons, captions, large SFX lettering and blank gutters.
   A *default* full-panel shot (`fallback_full`, `no_api_key_fallback`,
   `multi_beat_punchin`) gets a crop around it:
   * portrait art keeps its full width and crops the vertical band around the
     subject — the shape reviewers chose on Chapter 34; landscape art crops to
     the subject itself; the band grows until it is no wider than 16:9;
   * the crop must remove ≥15% of the picture and keep ≥15%;
   * on screen it must show the art ≥1.10× larger than the whole fitted panel;
     a tall *scrolling* panel may drop to ≥0.55× (reviewers accepted
     0.58–0.80× there to gain focus) — this refuses only extreme slivers.
   Planner close-ups (`vision`), composition-gate downgrades
   (`composition_full`), reviewer crops and any `manual_*` decision are never
   replaced. Multi-beat holds still get a small punch-in inside the subject
   frame.
2. **Turn** (only tall panels, height/width ≥ 1.8, *and* with no translated
   Latin lettering in their OCR — horizontal English text already proves a
   panel upright): the panel is shown as three copies (as source, 90°, 270°
   clockwise) side by side, asked twice with the copies in different orders.
   A turn counts only when both answers name the same turn at confidence ≥ 0.8.

Turns are **suggested** by default: the board shows finding F5 with an
*Apply suggested turn* button. With `AUTO_TURN=apply` they are applied
(`rotation_source: "auto"`, finding F6, undo with *As source*). Source pixels
are never rewritten: `panel_framing` applies the turn at render time.

## Measured on Chapter 34 (reviewer decisions as ground truth)

Ground truth: the 62-shot owner-review cut `…_34-liveqa-1018-edit2`, where a
reviewer turned 2 sideways bird spreads 90° clockwise and hand-cropped 15 shots.

| Check | Result |
|---|---|
| Turn suggestions (20 tall panels asked) | 1 of 2 sideways spreads found (`page016_panel_003`), 0 false suggestions among the 61 used panels; `page017_panel_005` missed (model reads its Korean SFX as upright) |
| Same check over all 62 tall panels in the chapter | 60/62 correct: the same miss, plus 1 false turn (`page005_panel_001`, a tilted action shot with no lettering) |
| Subject crops vs the 15 reviewer crops | mean IoU **0.68** vs 0.49 for the whole panel; 11/15 ≥ 0.6 |
| Misses | beat 66 (reviewer chose the moon the narration names; the subject box picked the character), beat 37 (a story-driven half-panel choice) |
| Real board (production-built `…_34-e2e-1018`, 76 segments) | 28 of 35 default shots reframed, 2 turn suggestions (seg 55 bird ✓, seg 15 false), reviewer/planner decisions untouched, one Undo restores the board |

Rejected approaches, measured the same way: a YOLO (COCO) uprightness vote
(wrong on drawn art, missed both spreads); a single "is this upright?" question
(0/2); one three-copy comparison without the lettering rule and order check
(7 false turns at 0.95 confidence). The existing `crop_score` composition gate
is **not** applied to subject crops: on Chapter 34 it rejected 21 of 50, mostly
good full-width bands it scores as "speech bubble"/"pinned to a corner", while
removing 3 crops that matched the reviewer (it did catch the beat-66 miss).

**Why turns are suggest-only:** a wrong turn shows characters sideways, which
is worse than leaving a panel alone. One chapter is not enough evidence for
`AUTO_TURN=apply`.

## Cost

Gemini `gemini-3.8-flash`, through the usage gate. Chapter 34 (61 panels, 20
turn checks): 101 calls, about **$0.34**. The evaluation for this feature
(all prototypes) cost $1.23 in total.

## Settings (Railway env vars)

| Name | Default | Effect |
|---|---|---|
| `AUTO_FRAMING` | on | `off` disables both questions (no spend) |
| `AUTO_TURN` | `suggest` | `apply` applies agreed turns; `off` skips the turn question |
| `AUTO_TURN_MIN_CONF` | 0.8 | confidence both comparisons need |
| `AUTO_FRAMING_MODEL` | `gemini-3.8-flash` | vision model |
| `AUTO_FRAMING_WORKERS` | 5 | parallel vision calls |

## Known limits / next

* Subject framing ignores the narration; when a beat names a different subject
  (the moon), the planner close-up path (`should_crop_close`) is the right tool.
* Turn detection needs more chapters before `apply`; Korean-SFX-only spreads
  are the hard case.
* Crops are judged on stills; the renderer's push-in/scroll motion is unchanged.
