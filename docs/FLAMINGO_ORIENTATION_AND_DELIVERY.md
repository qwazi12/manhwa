# Chapter 34 follow-up: delivery and picture orientation

October 18, 2026, America/New_York. Owner feedback: the 5:50 test is good but too condensed; use the saved Charon / Energetic recap host direction and check picture orientation/positioning before stitching.

## Verified facts

- Saved production default: Gemini, Charon, `energetic, fast-paced YouTube recap host`.
- Actual `..._34-liveqa-1018-edit1/tts.json`: Charon, `clear, dramatic storyteller; restrained urgency, natural pauses; avoid shouting`. The editorial run explicitly retained its own voice and reused 36 recordings, then recorded six changed sentences in that same restrained style. It did **not** follow the energetic default. This is an editorial choice that missed the owner's preference, not proof of a TTS provider ignoring a request.
- The 837-word / 42-shot / 5:50 result remains a technically passing test, not an accepted final episode. It removed too much context and breathing room. Signal QA and a clean transcript cannot certify the narrator's performance or story completeness; direct audible assessment remains incomplete.
- The pictured bird is `page016_panel_003`, new segment24, native **900×1924**, EXIF1. Neighbor `page016_panel_004` is900×2014/EXIF1. The first full-panel image has height/width2.138, just below the existing2.2 scroll threshold, and renders as a narrow card. The renderer did not turn a horizontal bitmap vertical. Neither native shape nor a tilted action composition alone proves the correct artistic rotation; compare source context before selecting a turn.
- A later Settings save restored1.1×. Only export_speed was restored to the owner's1.15× preference through the existing API; all other saved fields were compared and stayed identical. Before/after copies are private under `/app/data/.flamingo-orientation-review-1018`. The1.15 dropdown/fresh/manual defaults remain source changes in unmerged PR12.

## Built, offline tested, awaiting review

This extends the existing shot/crop/storyboard contracts; no extra AI generator, provider call or publishing gate was added.

1. `panel_framing` measures actual files and normalizes EXIF (including mirrored orientations). An explicit reviewer clockwise quarter-turn follows normalization; crop coordinates refer to that oriented image. Aspect ratio never triggers a guessed rotation or stretching.
2. Read-only pre-render checks flag a narrow full-panel card, a landscape source cropped into a portrait shot, and tall-scroll shots needing beginning/middle/end review. These framing findings are advisory. Invalid rotation values fail the render with a picture-specific error.
3. The current Next board receives measured geometry/findings and a reversible **Framing & orientation** control. Rotation requests name the chapter explicitly and bind to it, independent of another open/active chapter. Turning resets the old coordinate-system crop and invalidates only that shot's clip; Undo restores the prior manifest. The unmodified source remains linked.
4. Board previews, full-resolution segment images, staged HyperFrames assets and the fast renderer use the same EXIF → quarter-turn → crop order. Variant asset names avoid two differently turned uses of one panel overwriting each other. Preview keys and final-media input identities include the new framing state.
5. Both existing panel-swap paths now discard the previous picture's crop, saved planner crop, focus fields and rotation when artwork changes. Previously `crop_bbox_norm` could survive the swap, contradicting the existing comment and cropping unrelated art.

Renderer epoch4 ensures older-looking clips cannot silently satisfy a future render after the code is released. No batch render or deployment is triggered by this feature branch. Framing-code/input changes can make old QA stale; they must not be relabelled fresh without checking the exact new export. Semantic coverage, legacy-cache adoption and real-media calibration remain incomplete.

## Next Chapter 34 edit specification

Preserve the11:43 parent, both shorter exports, original/cancelled Chapter34 and four historical Waiting jobs. Produce any subsequent version as another explicitly marked QA sibling; do not overwrite edit1.

| Episode beat | Material to restore or protect | Editorial purpose |
|---|---|---|
| Opening / return | Keep the immediate rescue stake; give the returned enemy and possession reveal room to register | Establish who needs what, instead of jumping between spectacle |
| Threat and power | Restore the source-backed explanation of the stolen fifth-star body versus the enemy's effective danger | Make Eunhyun's tactical problem understandable |
| Wounded ally | Keep Young-hoon's condition, the witnesses' reaction and Eunhyun's decision connected | Let the audience feel why the delay matters |
| Evacuation | Restore the decision → handoff → movement toward help, using distinct relevant pictures | Avoid a compressed summary of an important character action |
| Golden attack / response | Preserve setup, scale, counterattack and consequence; review the bird source and the next shot together | An action sequence should read spatially and causally |
| Barrier / cliffhanger | Give the reveal and reaction a short intentional breath | End on a comprehensible reversal rather than an abrupt stop |

Provisional target **roughly7–8 delivered minutes**, not a hard word quota. Start around1,050–1,200 purposeful words, then derive the actual length from the recorded narration and scene needs. Restore selected explanation and reaction, not repeated blood/blast descriptions or the decorative border. Keep chapter-local Hwangshin consistency without silently rewriting global Bible canon.

Use the saved **Charon / Energetic recap host** configuration for all narration in that new version. Existing restrained recordings cannot be called energetic merely because the voice name matches; use the existing style-aware TTS cache/revoice path and record the actual voice/style in the new project. Energetic delivery still needs emphasis and pauses. Keep final export1.15×; do not add a second speed-up to compensate for a writing problem. A new full revoice/rerender was **not** launched in this follow-up.

Before another paid render: inspect the expanded source-linked script and start/middle/end framing previews, pin the delivery settings, check the remaining combined$5 allowance, confirm project-local QA distribution guards and preservation manifests, then review the exact MP4. No source/chapter/job/account/publishing edits are implied by this plan.

## Verification

Synthetic fixtures cover the bird's actual aspect ratio, native landscape/portrait/scroll cases, clockwise direction, rotated/mirrored EXIF, both-renderer/preview pixel agreement, a real H264 output frame, crop coordinate order, undo, selective clip invalidation, both swap paths, chapter binding, pre-render diagnostics and QA identity. **23 new tests / 245 combined Python tests pass**,53existing warnings. Crop-preview16/16, storyboard55/55, renderer epoch22/22 and fast-render15/15 pass; actual normal and rotated HyperFrames/fast-render comparisons both reach minimumSSIM0.991 across all frames. TypeScript and the final Next production build pass. One new test initially expected409 but the established gate returns400; its message was also corrected to describe picture rotation instead of an audio-timing fault, and the final combined run passed.

Fresh preservation checks match9protected job files,153original/cancelled Chapter34 files and418parentQA files, plus exact old queue hashes. No active jobs were reported before restoring only1.15×. No paid requests, revoice, live orientation edit, new project, new MP4, publication, upload, merge or deployment occurred. Scraped art/downloaded source files remain outside Git. See memory.md for operational receipts.
