# Chapter 34 editorial revision — completed live test

October 18, 2026, America/New_York. The owner requested pacing, name consistency and decorative-border repair, followed by rerendering and another review; the owner then required 1.15× video speed. The broader attached audit brief informs the quality criteria. A full library, autopilot and market audit was not completed in this focused revision.

[Watch the revised test in the current studio](https://manhwa.nodepilot.dev/chapter/a-regressors-tale-of-cultivation_34-liveqa-1018-edit1?tab=video).

**Owner follow-up:** the shorter test is good but too condensed. Its Charon recordings use the restrained project style, not the saved Energetic recap host style. Technical completion below does not mean creative acceptance. [Delivery/orientation findings and the next edit specification](FLAMINGO_ORIENTATION_AND_DELIVERY.md) record this correction and the reviewable framing implementation; no replacement MP4 has been generated in that follow-up.

## Result

| Item | Earlier live QA | Revised test |
|---|---|---|
| Script | 1,581 words / 78 voiced beats | **837 words / 42 beats**, retaining the major episode events |
| Delivered runtime | 11:43 (703.700 s) | **5:50 (349.866667 s)**, about 50% shorter |
| Speed | 1.10× | **1.15×**, verified in the render manifest and actual MP4 |
| Rescue stakes | Explicit evacuation dilemma arrived around 8:53 | Stated in the opening voice line |
| Villain name | Hwangshin/Wangshin alternation | **Makli Hwangshin** consistently; 9 Hwangshin occurrences, 0 Wangshin |
| Decorative border | 437×69 hatching fragment on screen around 5:46 | `page011_panel_001` absent from every selected segment and all 42 reviewed shot frames |
| Long holds | Two beats held one image for about 16.2 raw seconds | Longest delivered shot **11.277 s**; no delivered shot exceeds 12 seconds |
| Timing / voice | Earlier passing signal QA still had misleading excluded-placeholder errors | 42 of 42 clips, zero timeline errors, no silent segments, fresh final-media QA passes |

Final file: `final_Oct18_08.56PM_1.15x.mp4`, **117,905,898 bytes**, H.264/AAC, 1920×1080, 30 fps. Video 349.866667 s; audio 349.848 s; both start at zero. SHA-256: `ea1bf390f8702f1facca3da818e84685b63d9a1901a52f1c725f95d8874c0dd8`.

## What changed and why

The cut starts with the immediate conflict: Makli should be dead, and Eunhyun must delay him to give wounded Young-hoon a chance to escape. It then follows the source event order through revival/possession, the wounded ally, the fifth-star stolen body versus the enemy's dangerous effective strength, evacuation, the golden attack, the twenty-second sword form, and the sealing barrier/cliffhanger. Repeated blood-storm reactions, redundant boasts and the decorative transition sentence were removed. This is an explicit human-edited recap, not a new unsupported popularity or creativity score.

The chapter-local spelling follows this translation's Hwangshin form. The retained Wangshin line was corrected and revoiced; another repeated boast was omitted. Global Series Bible identity, the four existing name disputes and novel-wide canon were not rewritten. Consistent episode text does not independently settle every global romanization or kinship/honorific term.

Six selected narration lines were recorded again: the opening, fifth-star explanation, comparison with an ordinary cultivator, one villain boast/name, the final threat and the evacuation handoff. **36 unchanged voice files were reused**. Actual decoded audio durations rebuilt the timeline; no speech was accelerated locally beyond the final 1.15× export or cut short to force a duration.

The first 42-shot render still held a picture too long across two adjacent lines. The review caught it. Shortened the handoff to “Man-ho carries Young-hoon toward the Jin Clan healers,” preserved the first export and pre-edit timeline/audio, then rerendered **only the affected clip**, reusing the other 41.

The border remains in the preserved source material for provenance. It is excluded from the delivered cut; it was not deleted from history. The source-linked cut helper rejects reviewed forbidden panels, ambiguous/missing/duplicate prefixes, out-of-order selection, unverified speech text, escaped paths and attempts to overwrite the source project.

## Review actually performed

- Read the complete revised script and storyboard/source references.
- Downloaded and independently probed the actual final MP4; current-studio playback advanced from 0 to 25.13 seconds with ready state 4 and no media error.
- Inspected a frame at the exact midpoint of **every one of the 42 shots**. This is a per-shot visual review, not a claim to have manually watched every frame in real time.
- Ran a black-frame interval check over the full video; no intervals of at least 0.2 seconds were detected at the chosen threshold. This does not certify every transition artistically.
- Ran whole-track local transcription with the already-cached Whisper model: 75 transcript segments, 45.3 seconds of processing, no model download or API call. Read the full transcript and checked the opening, power distinction, counterattack and barrier ending.
- Fresh final-media report: full decode passed, zero critical findings and one source-voice pause warning around **5:07**. The reported 1.04-second source pause is approximately 0.90 seconds after speed adjustment. It occurs before the barrier explanation; it remains visible and was not suppressed.
- Ran the actual free rules validator against this new project: status OK, **zero findings, zero model calls**.

The episode is materially tighter and its central choice is much clearer. The cut now has a coherent rescue setup, escalation, counterattack and trap ending. The visual language still relies heavily on tall centered panels and blurred backgrounds; it is not yet premium scene-aware direction. **Direct audible vocal tone and pronunciation are not verified by the current tool.** ASR misspellings or a possible extra word are not ground truth and were not used to trigger speculative paid retries. Semantic speech QA remains `not_run`; thresholds remain uncalibrated.

The studio's summary runtime and two hold advisories still use pre-speed timeline values. The player/MP4 duration and measured delivered shot lengths above are authoritative. This display defect is documented in the video's review notes; no thresholds were relaxed.

## Packaging and isolation

Copied the already-reviewed Chapter 34 title, description, specific tags and clean CH 34 cover thumbnail to this final export. Preserved the automatic payload before correction. Selected title: **He Must Hold the Line | A Regressor's Tale of Cultivation Chapter 34**. Thumbnail: 1280×720 JPEG, 136,995 bytes, no store advisories. This retains existing series branding; it is not claimed to be CTR-tested or the best possible episode thumbnail.

The test is private, has no publishing targets or publish time, and remains marked `.flamingo-qa-only`. An actual approval attempt returned HTTP 403. No publishing, scheduling, Drive upload, account or publishing-destination change occurred. The new project's Drive record is absent. Metadata corrections apply only to this test export, not global series defaults or published videos.

Estimated incremental cost recorded for this revision, including automatic packaging: **$0.106763** at the final measurement. Six narration edits account for approximately $0.013318. Combined with the earlier local/live tests, the recorded estimate remains about $2.05 of the existing $5 allowance. These are application estimates, not provider invoices; no budget cap was increased.

## Preservation and production boundaries

The original `_34` and cancelled `_34-upd-1018` were untouched. Their **153 protected files** and **9 protected job files** match their baselines. All **418 files** in the preceding `_34-liveqa-1018` QA project also match its pre-revision hashes. The first new editorial MP4 remains hash-identical and its pre-handoff-edit inputs are archived.

All four historical Waiting records remain unchanged: `684b271aa3b2`, `97b762094d26`, `ea244d5ffbb8`, `e7deae78a155`. No resume, retry, cancellation or deletion was performed. Original cancelled job `c5b1067541f1` remains cancelled. Final active/running worker counts are zero.

Used the existing direct board-render workflow after verifying production idle, so no historical render-queue entry was added or pruned. Both posting and render queue bytes remain unchanged. The only approved global setting change is export speed 1.10→**1.15**; every other settings field is identical. Before/after settings and a new approved state-hash manifest are retained privately without overwriting the old baseline.

**1.15× is saved in production for normal future studio renders without a restart.** The source changes adding the Settings dropdown option, default for fresh installations and omitted-speed manual-export handling are on the feature branch for review; they have not been merged/deployed. The production application release remains `5fec5c7`. The checksum-pinned editorial helper was used only as an operational tool for this isolated revision, not installed over the running backend.

## Engineering verification

- **222 Python tests pass**, 53 existing deprecation warnings, using the repository's def-test files in normal deterministic order.
- Settings script: **15/15**, including 1.15× without publication/schedule changes.
- Approve/revoice script: **21/21**.
- Actual export-swap checks: **6/6**.
- TypeScript and Next production build pass.
- New source-linked editorial tests cover immutable inputs, explicit name correction, decorative exclusion, actual-duration retiming, separate identity and fail-closed invalid inputs; the manual export default is tested too.

A broad unfiltered pytest invocation first encountered the repository's legacy import-time `SystemExit`; selecting def-test files follows AGENTS.md. An unsorted exploratory run also exposed an existing fake Upload-Post profile environment leak between tests; the normal deterministic order passes. That test-independence issue remains a harness cleanup item, not a production account change. Two operational bootstrap attempts failed at compile/import time before any TTS request or project mutation; the successful run's receipts and costs are preserved.

## Remaining work

This completes the requested Chapter 34 pacing/name/border revision and rerender/review within the available visual, transcription and signal tools. A direct listening pass is still needed for vocal performance and proper-name pronunciation. Premium framing, shared reviewed episode terminology/events, semantic coverage, legacy-cache adoption and calibration remain separate work. The broader attached inventory/autopilot/library/SEO/research audit has not been represented as completed by this focused episode repair.

## Diagnostic context note

A direct CLI media-status call through `/app/data/projects` reported stale while the actual production HTTP API remained passed/fresh. Compared the evidence: the audio bytes have identical hashes, but the captured path uses `/app/manhwa-recap-v1/review_ui/projects` (the service symlink) and the CLI used `/app/data/projects`. Path strings participate in the input digest. The final recorded result is the production API's passed/fresh identity, matching the downloaded MP4 hash; no manifest was recaptured or freshness check suppressed. Canonical path handling deserves an offline regression before changing historical identity contracts.

## Review checkpoint and retained media

[Draft PR 12](https://github.com/qwazi12/manhwa/pull/12) contains the tested source changes and this evidence, stacked on documentation PR 11. GitHub main remains `5fec5c7`; no new merge or application deployment occurred. Memory and all checkpoints are pushed to `dev/flamingo-ch34-editorial-revision`.

The Watch UI's normal export retention remains unchanged. To preserve the QA versions beyond automatic export cleanup, verified three independent MP4 copies plus QA sidecars/index (465,493,591 bytes total) under the private volume path `/app/data/.flamingo-editorial-v2/retained-media`. This includes the preceding 11:43 video and both new editorial exports. No source project or historical job was rewritten to make these copies.
