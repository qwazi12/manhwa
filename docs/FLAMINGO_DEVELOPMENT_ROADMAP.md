# Flamingo Development Roadmap

Owner-authorized October 18, 2026. Implement offline on feature branches, log significant work to `manhwa-recap-v1/memory.md`, and push development checkpoints to GitHub. Never deploy, push/merge main, call paid AI, mutate production, change accounts, or publish/schedule real content without separate authorization.

The creator-studio purpose is preserved in [the vision](FLAMINGO_STUDIO_VISION.md). The October 18 audit's F1–F8 findings establish the initial repair scope. Its full report is private outside Git; source-grounded findings and baseline results are summarized in memory. Starting code: `40d67cd`; audit history commit: `f8e2da3`.

Allowed task states: **Not Started / In Progress / Blocked / Testing / Ready for Review / Complete**. Ready for Review means implemented and tested offline, with remaining calibration/release conditions explicit. Complete must not imply production verification that did not occur.

## Milestone overview

| Milestone | Priority | Dependency | Status | Evidence and remaining boundary |
|---|---|---|---|---|
| M1 Final media audio QA and export readiness | Critical | Existing renderer/timeline contracts | Ready for Review | Offline implementation/tests/UI verified; real-media calibration and deployment approval remain required. |
| M2 OCR dialogue and narrative reliability | High | M1 offline checkpoint; safe fixes separable | Ready for Review | Ordered typed transfer, orphan visibility and truthful critique lifecycle implemented offline. |
| M3 Cache freshness and source completeness | High | M2 provenance contracts; cache fixes separable | Not Started | F6 same-size replacement reproduced. |
| M4 Narration voice and pacing | High | M1 measurements; real-media decisions | Not Started | Current pauses/speed remain unchanged pending evaluation. |
| M5 Scene-aware visual editing | High | Reliable provenance and source QA | Not Started | Preserve renderer; prototype reversible presets. |
| M6 Demand vidIQ SEO and growth intelligence | Medium | Channel identity; quality baseline | Not Started | No account changes or paid research authorized. |
| M7 Shorts Reels and community content | Medium | Approved reliable long-form; format standards | Not Started | Authoring prototypes only; no public posting. |
| M8 Audience analytics and learning | Medium | Authorized intended-channel analytics | Not Started | Current connection does not authorize intended private analytics. |

## M1 Final media audio QA and export readiness

**Purpose:** repair F3/F4 and add actual final-file inspection. Structural JSON validity must not imply audible complete narration or a valid export. Reuse existing timeline/source validators.

| Task | Status | Files | Acceptance and required tests |
|---|---|---|---|
| M1.1 Shared beat/slice audio resolver | Ready for Review | shared audio helper, `chapter_qa.py`, renderer, storyboard validator | Standard index, explicit relative filename and nested slice resolve consistently; missing/corrupt inputs visible; path regression tests. |
| M1.2 Decoded voice diagnostics | Ready for Review | focused media QA module | Inspect silence/activity, missing/broken voice, excessive pauses, clipping and truncation evidence; deterministic real FFmpeg fixtures including short voiced content and deliberate silence. |
| M1.3 Final MP4 inspection and timestamp map | Ready for Review | media QA, export/range paths | Inspect after BGM/loudness/speed; decode streams and compare delivered schedule; map cards, omitted segments, range joins and 1.0/1.25x; music cannot satisfy absent expected voice. |
| M1.4 Versioned report and media identity | Ready for Review | media QA, `server.py` review/signatures | Digest checker inputs and exact export; changed audio/text/timing/images/settings/export invalidate freshness; preserve legacy records with explicit migration status. |
| M1.5 Readiness contract everywhere | Ready for Review | direct/social/worker/scheduled/range gates | Locally enforce failed/incomplete critical checks with zero backend upload calls; default policy diagnostic-only; no new production blocking without calibration and approval. |
| M1.6 Existing Chapter Video and Issues UI | Ready for Review | `web/components/chapter/` | Display checked/incomplete/stale state, evidence timestamps, affected beats and recommended focused repairs; safe local run control without AI calls. |
| M1.7 Regression and release documentation | Ready for Review | tests, handbook, roadmap, memory | Relevant existing tests + new media tests; fix F8 global test leakage; tsc/build; record exact results and skipped checks. |

**Completion evidence:** 40 pytest tests pass in one process, including new generated-media regressions and the formerly leaking render-isolation sequence. Twelve selected existing script suites pass (421 recorded assertions, not end-to-end videos). TypeScript and Next.js build pass. Local browser verification confirmed inspect → POST/GET API → SHA-bound saved report → pass/stale/failure display in Video and Issues. The only browser console error was the existing missing favicon. Python static checks pass. See handbook E26 for limits and rollout. The final renderer parity rerun is recorded in memory.

**Risks and decisions:** energy/activity is not semantic word verification; no automatic trimming or regeneration; intentional pauses/cards need explicit representation. Calibrate thresholds on one defective and one approved-good real recap before enabling enforcement. No local finished recap MP4 was available in the audit. Rollback is additive-report preservation plus reverting isolated commits; default-off gate preserves production behavior.

## M2 OCR dialogue and narrative reliability

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M2.1 Ordered structured dialogue transfer | Ready for Review | `panel_stats.py`, `ocr_lines.py`, `narrate.py` | Fix F1/F2; typed lines and legacy strings preserve source order, speakers and line identity; multiple bubbles, mixed representations, repeated calls and backward transfer tested. |
| M2.2 Orphan speech handling | Ready for Review | narration/provenance, validator/UI | Text with no safe art target remains explicit and reviewable; no silent loss or fabricated attribution; page-edge/all-text cases tested. |
| M2.3 Truthful critique lifecycle | Ready for Review | `narrate.py`, critique UI | F5: malformed/skipped/incomplete differs from clean; bounded repair attempts, unresolved defects tracked; cap and error tests with fake providers. |
| M2.4 Canonical revision context | Ready for Review | `narrate.py`, Series Bible integration | F7: revisions retain original Bible/opening/story inputs; deterministic prompt/context and final-audit tests. |

**Evidence:** 49 combined pytest tests passed before the final speaker-preservation regression; all 20 M2-specific regressions then passed. Eight existing script suites passed (265 recorded assertions); impacted roles/OCR/direct-speech suites were rerun after the last source-line change. TypeScript/static checks and Next.js build pass. Local browser confirmed orphan text and incomplete-review warnings through the real API. No generated narrative quality claim: model calls were fakes; real creative calibration remains outstanding. **Risks:** approved creative tone and real narrative quality need source/media evaluation; do not remove spending caps to improve apparent completion. **Rollback:** isolated code revert; preserve source identities and old artifact readability.

## M3 Cache freshness and source completeness

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M3.1 Image content fingerprints | Not Started | `panel-describe/run.py`, `describe.py` | F6: same-size changed pixels invalidate OCR/descriptions; unchanged valid cache reusable; offline mocked-service regression. |
| M3.2 Generation manifests | Not Started | ingest/narrate/TTS/cache helpers | Track source/Bible/prompt/model/voice inputs; changed dependencies stale descendants; retain valid content-keyed TTS; no paid regeneration in tests. |
| M3.3 Editorial preservation and invalidation | Not Started | `pipeline_steps.py`, review/signatures | Preserve user edits/approved originals; explicit stale/review state; no destructive automatic refresh. |
| M3.4 Source-line and event coverage | Not Started | provenance, validator, Issues | Meaningful unrepresented OCR/events appear as uncertainty findings; deterministic line coverage first; semantic claims require evaluated evidence. |

**Evidence:** pending. **Risks:** legacy caches lack manifests; adopt conservative explicit status rather than silently assume freshness or regenerate. **Rollback:** additive schema; retain previous media and edits.

## M4 Narration voice and pacing

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M4.1 Boundary/pause measurements | Not Started | media QA, ingest timing | Compare scheduled gaps and decoded activity; intended pauses remain distinct; controlled samples. |
| M4.2 Timing intent and focused repairs | Not Started | storyboard editor, voice operations | Per-scene intent, previewable targeted repairs, no word truncation/overlap or entire-chapter paid rerun. |
| M4.3 Controlled voice/speed evaluation | Blocked | voice/style settings, sample evaluation | Owner-approved real samples compare normal/current speed, clarity/emotion and pronunciation; no defaults changed before approval. |

**Evidence:** audit measured historical local voice files; not final exports. **Risks:** artistic pause/voice standards undecided. **Rollback:** reversible per-sample overrides; preserve global defaults.

## M5 Scene-aware visual editing

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M5.1 Editorial shot-plan prototype | Not Started | shot planner, segments | Explicit action/dialogue/reaction/reveal/atmosphere purpose tied to source; deterministic schema and fallback. |
| M5.2 Restrained preset prototypes | Not Started | existing FFmpeg/HyperFrames renderers | Readable tall panels/crops, correct visual emphasis, deliberate motion variation; synthetic frame/audio parity tests. |
| M5.3 Creative comparison | Blocked | private sample evaluation | Owner compares proposed and existing output with approved reference media; no global preset switch without approval. |

**Evidence:** current renderer mechanical media tests pass. **Risks:** scene labels and creative standards need evaluation. **Rollback:** opt-in prototype plans with existing renderer fallback.

## M6 Demand vidIQ SEO and growth intelligence

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M6.1 Metric labeling and observations | Not Started | `demand_research.py`, `yt_api.py`, research UI | Lifetime averages named honestly; valid snapshot deltas and same-age/format comparisons; fake-client tests. |
| M6.2 Canonical channel identity | Blocked | settings, SEO cache, publishing profile | Owner confirms channel IDs for handles/profile; no automatic reconnect or destination changes. |
| M6.3 Evidence adapters and ranking | Not Started | existing research modules, optional vidIQ adapter | Timestamp, sample scope, audience fit/source effort and uncertainty; fixtures only; explicit credit controls before real requests. |
| M6.4 Packaging experiment records | Not Started | SEO/thumbnail records, analytics | Separate distinct audience promises, heuristic scores, observed outcomes and causal uncertainty; fake analytics tests. |

**Evidence:** audit found lifetime-average thresholds and no repository vidIQ integration. **Risks:** private intended-channel data unavailable; no speculative scores. **Rollback:** additive observations and opt-in adapters.

## M7 Shorts Reels and community content

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M7.1 Approved-source content package | Not Started | new draft asset planner using provenance | Two distinct mini-stories, discussion/poll drafts, objective and original-source links; no merely cropped horizontal reposts. |
| M7.2 Vertical layouts captions and QA | Not Started | existing renderer extensions, preview UI | Platform-appropriate hooks/crops/pacing/readability; deterministic synthetic previews and caption timing tests. |
| M7.3 Approval/publishing integration | Not Started | existing queue/approval controls | Independent asset QA/approval; no automatic real posts; mocked backend refusal tests. |

**Evidence:** transport exists; authoring absent. **Risks:** creative format standards and platform details require evaluation. **Rollback:** draft-only opt-in assets; original long-form unchanged.

## M8 Audience analytics and continuous learning

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M8.1 Authorized analytics ingestion | Blocked | analytics adapter, existing public stats | Intended-channel impressions/CTR/retention/watch time/subscriber/returning-viewer records; distinguish unavailable from zero; fixture tests. |
| M8.2 Comment and correction evidence | Not Started | new draft insights linked to existing validator feedback | Deidentified requests/complaints and personalized reply drafts; no unsolicited public messages; fake inputs. |
| M8.3 Unified briefing and learning | Not Started | existing Home/Activity/Chapter surfaces | Trace recommendations to observations and approved corrections; same-age cohorts and uncertainty; no causal guarantees. |

**Evidence:** public views/likes exist; private feedback loop absent. **Risks:** account authorization and privacy; avoid public commit of private analytics. **Rollback:** additive read-only/draft integrations.

## Checkpoint log

- October 18: owner authorization recorded; roadmap/vision created; development-only Vercel Git deployment guards and persistent agent instructions prepared. Next: M1 shared resolver and decoded diagnostics. All production behavior remains unchanged.

- M1 offline checkpoint: resolver, diagnostics, export manifests, review/readiness contracts and UI implemented. 40 combined pytest tests and 421 selected existing script checks pass; tsc/build and isolated browser flow verified. Production enforcement remains OFF. Next: M2 dialogue/provenance and critique reliability.

- M2 checkpoint: F1/F2/F5/F7 repaired offline; M2 Ready for Review. Source references mean available evidence, not proven semantic coverage. Next safe work: M3 content fingerprints with explicit legacy-cache handling and editorial preservation.
