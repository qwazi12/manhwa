# Flamingo Development Roadmap

Owner-authorized October 18, 2026. Implement offline on feature branches, log significant work to `manhwa-recap-v1/memory.md`, and push development checkpoints to GitHub. Never deploy, push/merge main, call paid AI, mutate production, change accounts, or publish/schedule real content without separate authorization.

The creator-studio purpose is preserved in [the vision](FLAMINGO_STUDIO_VISION.md). The October 18 audit's F1–F8 findings establish the initial repair scope. Its full report is private outside Git; source-grounded findings and baseline results are summarized in memory. Starting code: `40d67cd`; audit history commit: `f8e2da3`.

Allowed task states: **Not Started / In Progress / Blocked / Testing / Ready for Review / Complete**. Ready for Review means implemented and tested offline, with remaining calibration/release conditions explicit. Complete must not imply production verification that did not occur.

## Milestone overview

| Milestone | Priority | Dependency | Status | Evidence and remaining boundary |
|---|---|---|---|---|
| M1 Final media audio QA and export readiness | Critical | Existing renderer/timeline contracts | Ready for Review | Offline implementation/tests/UI verified; real-media calibration and deployment approval remain required. |
| M2 OCR dialogue and narrative reliability | High | M1 offline checkpoint; safe fixes separable | Ready for Review | Ordered typed transfer, orphan visibility and truthful critique lifecycle implemented offline. |
| M3 Cache freshness and source completeness | High | M2 provenance contracts; cache fixes separable | In Progress | Cache/source-preservation and deterministic linkage tasks Ready for Review; semantic assessment and legacy adoption remain open. |
| M4 Narration voice and pacing | High | M1 measurements; real-media decisions | Not Started | Current pauses/speed remain unchanged pending evaluation. |
| M5 Scene-aware visual editing | High | Reliable provenance and source QA | Not Started | Preserve renderer; prototype reversible presets. |
| M6 Demand vidIQ SEO and growth intelligence | Medium | Channel identity; quality baseline | Not Started | No account changes or paid research authorized. |
| M7 Shorts Reels and community content | Medium | Approved reliable long-form; format standards | Not Started | Authoring prototypes only; no public posting. |
| M8 Audience analytics and learning | Medium | Authorized intended-channel analytics | Not Started | Current connection does not authorize intended private analytics. |

## TV-production expansion and dependency order (October 18 addendum)

The original eight milestones and their existing evidence/statuses remain. The product journey starts with **external research → Series Intelligence → Episode Intelligence → scripting/voice/editing → QA → truthful packaging → exact approved publication → audience feedback → renewed research**. Milestone numbers organize engineering work; they do not put research after production in the user workflow.

The new [intelligence design/source assessment](FLAMINGO_INTELLIGENCE_DESIGN.md) defines the shared records, evidence/opportunity contract, selective-invalidation targets and reuse/repair/build inventory. This checkpoint updates requirements and feasibility only; no new Research Investigator/Series/Episode service, live collector or UI workspace has been implemented. Existing M1/M2/M3 feature work is preserved.

Finish current M3 offline work before dependent consumers. Safe M3.1–M3.4 hardening/regressions are finished for this checkpoint. M3.5/M3.6 remain blocked on reviewed evidence/owner decisions; do not force paid rewrites or invent semantic verdicts to clear them. The next independent safe task is a read-only, fixture-tested Series/Episode identity/evidence projection prototype (M3.7/M3.8), without adopting legacy artifacts or wiring broad external services. Labeled-coverage evaluation/adoption design can proceed without claiming those missing decisions are resolved. Add M3.7–M3.10 as the shared foundation before M4/M5 story-aware consumers and M6/M7 evidence-dependent packaging/assets. Source-feasibility documentation and independent regression repairs may proceed without triggering paid research. Real calibration/account/source-access decisions stay explicit; no new scheduled investigator is enabled.

| Addendum requirement | Backlog ownership | Acceptance boundary |
|---|---|---|
| Genuine external Investigator, not just watchlist lookups | M6.1, M6.3, M6.5–M6.9 | Discover a previously untracked series from a broader topic; persist cited observations, alternatives, disagreements and missing evidence; no invented demand metrics |
| Shared Series Intelligence | M3.7, M3.10; M6.6 | Extend Bible/pack/provider identity, verified aliases, continuity and history; market theory cannot write canon |
| Connected Episode Intelligence and focused invalidation | M3.5, M3.8–M3.9 | Ordered source/line/panel/event/scene evidence feeds all ten consumer classes; only affected outputs stale; operator edits preserved |
| Mutually aware writer/editor/voice | M4.2, M5.1, M3.8 | Sentence/event/shot links and intentional rhythm; no random matching or unsupported story invention |
| Evidence-led SEO and packaging | M6.4, M6.10–M6.11 | Distinct truthful concepts, evidence per promise, opening fulfillment, correct metadata and labeled editorial scores/observed outcomes |
| Media-resource assessment | M6.12–M6.14 | Verify primary application capabilities and actual local integration; catalog ≠ demand; unseen video remains unassessed |
| Connected website | M6.15, M3.8, M7.1–M7.3, M8.3 | Extend existing seven functional workspaces/current navigation, with shared evidence links and independent asset approvals |
| Feedback/one-episode promotional package | M7.1–M7.3, M8.2–M8.3 | Approved parent source/export, distinct original formats, own QA/approval; learning suggests canon/editorial corrections for review |

## Current review readiness and blockers

| Scope | State | Evidence or missing requirement |
|---|---|---|
| M1.1–M1.7 offline implementation | Ready for Review | Resolver, decoded/final-media diagnostics, exact identities, readiness paths and UI; recorded media fixtures/build/browser regressions. No deployment or claim of calibrated production quality. |
| M2.1–M2.4 offline implementation | Ready for Review | Ordered OCR/speaker/source-line fidelity, orphan visibility, strict critique lifecycle and canonical revision context; regressions recorded. |
| M3.1–M3.4 offline implementation + hardening | Ready for Review | Content-aware description/script/voice reuse, source/rerun preservation, truthful source linkage and incomplete-stage stops; 104 selected combined pytest tests pass. |
| M3.5 semantic coverage | Blocked | Owner-reviewed labeled source/script corpus and creative omission/paraphrase decisions missing; references alone cannot prove completeness. |
| M3.6 legacy adoption | Blocked | Representative legacy bundles and explicit review/attestation policy missing; preserve unknown artifacts, do not invent generation history or trigger paid refresh. |
| M1 production calibration/enforcement | Blocked | Approved defective and good real final-MP4 bundles plus owner approval to enable; MEDIA_QA_ENFORCE remains 0 by default. |
| Broad external collection/private analytics | Blocked | Owner approval for integrations/costs, provider access and intended-channel identity required. Plan/fixture work is permitted; no account/destination changes. |

## Working features, repairs, and new development

- **Already working and preserved:** production background splitter/fallback; structured OCR/speaker basis; Series Bible; anchored narration and constrained matching; targeted timeline/slice/crop repairs; content-keyed TTS cache and pinned voice; FFmpeg renderer plus HyperFrames alternative; review, range, SEO/thumbnail and publishing transport; trash, release wait, duplicate prevention and budget gates.
- **Repairs implemented offline:** F1/F2 dialogue loss/order, F3 audio QA paths/heuristics, F4 exact-export freshness support, F5 incomplete critique reporting, F6 dimension/presence-only caches, F7 missing revision context, and F8 leaking test globals. Each has targeted regression evidence; production has not received these changes.
- **New capabilities built offline:** final-file diagnostics and mapped findings, default-off readiness policy, source orphan/linkage review, versioned generation identities, staged source replacement and recoverable rerun archives.
- **Genuinely still needs building/evaluation:** semantic narration completeness, legacy adoption UX, calibrated creative voice/pacing and scene editing, intended-channel identity/analytics, recent-demand observations, original repurposed assets and audience-learning workflow. Remaining tasks below retain their actual status.

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
| M3.1 Image content fingerprints | Ready for Review | `panel-describe/run.py`, `describe.py`, `generation_cache.py` | F6: SHA-256 of actual image bytes plus engine/model/effective Bible prompt/code; same-size changes stale; verified unchanged descriptions reuse without calls; stale/unknown data preserved before any description requests. |
| M3.2 Generation manifests | Ready for Review | ingest, `generation_cache.py` | Script source/Bible/effective direct-speech/opening/model/code inputs and exact output hashes; voice speakable text/config/function identity and per-beat hashes; fresh reuse makes zero repeat generation calls, stale/edited/unknown stops before that generation stage. Existing global content-keyed TTS cache preserved. |
| M3.3 Editorial/source preservation | Ready for Review | ingest, `pipeline_steps.py`, cache helper | Explicit reruns archive scripts, edits, approvals, voices, slices, manifests and clips; exports untouched. Download/split staged; failed downloads leave active sources intact; changed successful source trees archived before promotion; partial archive failures recoverable from index. |
| M3.4 Deterministic source linkage | Ready for Review | `source_coverage.py`, server, Issues | Eligible OCR source-line IDs/text and artwork panel IDs without spoken provenance links become review candidates; changed/missing/legacy references, silent scenes, original bubble identity, non-story/SFX exclusions and project-scoped read-only API tested. Always semantic_coverage=not_assessed, even when all references exist. |
| M3.5 Semantic story coverage evaluation | Blocked | source/script evaluation fixtures and checker | Determine whether meaningful events/dialogue were actually conveyed, including paraphrases and supported omissions; linkage alone cannot satisfy this. Blocked on owner-reviewed labeled source/script examples (supported paraphrases, important omissions and accepted creative standards) before any semantic correctness claim or gate. Existing deterministic linkage remains Ready for Review. |
| M3.6 Reviewed legacy-cache adoption | Blocked | migration/review workflow | Preserve reviewed historical artifacts without forcing paid rewrites; an explicit adoption must record uncertainty/owner review rather than retroactively assert historical inputs. Blocked on representative historical artifacts and owner decisions about review/attestation and preservation; no historical input identity can be guessed. Existing unmanifested caches stop with targeted-rerun guidance; no automatic migration or paid refresh. |
| M3.7 Series Intelligence identity/view | Not Started | `series_bible.py`, `series_pack.py`, watchlist/providers/research | First repair the confirmed Unicode/empty-alias Bible lookup collision using isolated fixtures and no historical re-keying; then stable IDs for existing and pre-ingest candidates; verified English/Korean/romanized/abbreviated aliases with provenance; character/world/relationship/term/canon revisions, releases, approved branding and production history joined from existing stores. Test unrelated Korean titles cannot select a built-in Bible, exact known aliases, ambiguous prefixes, no implicit seeding in read-only views, same title/different work, adaptation relations, owner edits and no duplicate canon store. |
| M3.8 Episode Intelligence projection | Not Started | descriptions/OCR, narrate/provenance, storyboard/QA/packaging adapters | Preserve ordered pages/panels/images/lines/speaker basis; evidence-bound events, reactions, intent/consequences, scenes/transitions, reveals/stakes/hooks, locations/characters, uncertainty and corrections. Bind Bible/source revision; all ten consumer classes in design read the same record; compilation retains child IDs/offsets. Test source corrections, disputed speakers, intentional omissions, unavailable evidence and consistent consumer revisions. |
| M3.9 Selective dependency invalidation | Not Started | generation cache, provenance, TTS/segments/render/SEO/thumbnail approvals | Field/input-level dependency graph and affected-output preview; metadata-only changes do not revoice/re-OCR, source/script changes stale affected descendants, retain unaffected content-keyed voice and owner edits. Test market-only/title-only/source-pixel/script/timing/thumbnail mutations and exact-export/packaging approval separation; no hidden paid refresh. |
| M3.10 Canon/research separation | Not Started | Bible/research/pack merge contracts | Separate observations, interpretations, market evidence, hypotheses, approved canon and correction history with source/review dates; forum/competitor claims cannot silently become canon. Test denied promotion, conflicting sources, owner approval precedence, spoiler/chapter bounds and no unrelated canonical mutation. |

**Evidence:** Original M3 checkpoint: 99 combined pytest tests passed. Follow-up hardening: **104 selected combined pytest tests pass**, including **32 cache/source-preservation** and **12 source-linkage** tests alongside M1/M2/render/publish regressions. Two new edge-case failures reproduced before fixing retained-stale status under caps and completed-progress loss on unexpected panel failure; incomplete batches now exit before narration. Actual ingest boundary and partial-promotion recovery regressions pass. Pipeline strip19, hardening54 and TTS config33 assertions pass (106 total). Python static/compile checks pass. The unchanged UI retains the prior M3 TypeScript/build/browser evidence. Local browser/API confirmed the expandable source candidate with original text and explicit semantic uncertainty; only existing favicon404. No real service/model calls or real chapter regeneration. Final text-only UI build rerun recorded in memory.

**Risks and release decisions:** M3 is still In Progress because semantic coverage and legacy adoption are not built and await the reviewed evidence/decisions described above. Conservative code fingerprints intentionally mark caches stale after relevant generation-code changes. Legacy outputs cannot be proven fresh retrospectively; do not automatically regenerate/adopt them on release. Archives/staging remain on disk with no automatic retention deletion; plan disk/restore UX before production rollout. Source promotion is recoverable but not a transaction spanning both directories; partial disk errors require inspection of index/staging. No live migration was run. **Rollback:** isolated code revert; additive metadata and archives remain, with originals recoverable by restoring indexed relative paths while jobs are stopped.

## M4 Narration voice and pacing

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M4.1 Boundary/pause measurements | Not Started | media QA, ingest timing | Compare scheduled gaps and decoded activity; intended pauses remain distinct; controlled samples. |
| M4.2 Timing intent and focused repairs | Not Started | storyboard editor, voice operations; M3.8 | Per-scene emotional/narrative intent and approved pronunciation/terms flow from Episode/Bible evidence into TTS; previewable focused repairs; no word truncation/overlap or entire-chapter paid rerun. Test sentence/scene intent continuity and preserved unaffected audio. |
| M4.3 Controlled voice/speed evaluation | Blocked | voice/style settings, sample evaluation | Owner-approved real samples compare normal/current speed, clarity/emotion and pronunciation; no defaults changed before approval. |

**Evidence:** audit measured historical local voice files; not final exports. **Risks:** artistic pause/voice standards undecided. **Rollback:** reversible per-sample overrides; preserve global defaults.

## M5 Scene-aware visual editing

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M5.1 Editorial shot-plan prototype | Not Started | shot planner, segments; M3.8–M3.9 | Writer sees source art/dialogue, editor sees sentence/event meaning; explicit action/dialogue/reaction/reveal/atmosphere purpose, intentional crops/timing and transitions tied to common evidence. Test unsupported-event refusal, source/event/shot links, readable crops and deterministic fallback. |
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
| M6.5 Investigator evidence archive | Not Started | extend research/SEO research and existing private series/project storage | Bounded source plans start from genre/topic or unknown series, not only watchlist/uploads. Persist URL/provider/query, observed/published/event dates, locale/window/metric units, permitted payload/excerpt/hash, provenance and access/uncertainty before synthesis. Tests: unseen-series discovery, partial/unavailable source, duplicate underlying news, no secret/private payload in Git and no unauthorized/paid adapter call. |
| M6.6 Verified cross-source aliases | Not Started | series pack/Bible/provider identity (M3.7) | Verify English/Korean/original/romanized/abbreviated identities with sources; unresolved matches stay separate; distinguish adaptations and character names. Tests: ambiguous abbreviations, duplicate spellings, alias collisions, title rename without ID change and multilingual identity. |
| M6.7 Official catalogs/releases/news adapters | Not Started | provider/research adapters | Official publisher catalogs/rankings/new releases, genre discovery, dated returns/hiatuses/adaptations; record locale and access limits. Catalog rank is not video demand or reuse permission. Fixture tests for changing ranks, unknown dates, blocked/JS-only pages, superseded announcements and lawful-material availability remaining unknown. |
| M6.8 Community/search-interest adapters | Not Started | research adapters and evidence contract | Permitted Reddit themes and accessible Google Trends/comparable signals remain distinct from story canon/YouTube views. Record query/topic, region/window/search surface/scaling; access/terms and price checks before real calls. Test missing access vs zero, sparse samples, incompatible normalization, deidentification and rejected forum-to-canon promotion. |
| M6.9 Opportunity comparison | Not Started | demand/research ranking and Research Desk view | Every opportunity includes candidate/topic, reasons, cited dated supporting/conflicting evidence, measured vs inferred facts, verifiable momentum, comparable video age/format, sampled competition, demand category/reason, audience, lawful chapter/material availability, effort basis, risks/gaps and why now versus named alternatives. Tests reject fabricated volumes/velocity/forecasts, lifetime-average-as-momentum, unsupported saturation and single-channel-only discovery. |
| M6.10 Story-grounded concept sets | Not Started | `seo.py`, `series_hooks.py`, `chapter_seo.py`, `thumbnail_studio.py`; M3.8 | Separate market research, episode truth and packaging strategy. Multiple distinct event/theme-backed title+thumbnail promises; evidence per clause/image; clarity/specificity/audience/mobile/distinctiveness/search/opening fulfillment rubric. Preserve locked series hooks/owner choices; drafts do not retitle prior videos. Test unsupported hook/image and opening mismatch; scores never labeled actual CTR. |
| M6.11 Metadata and experiment linkage | Not Started | existing SEO, playlist, thumbnail/experiment records | Accurate description, canonical/alternative titles, chapter/range, tags/hashtags and playlist; observed authorized CTR/watch time/retention/returning viewers/tests separately labeled by sample/window. Test range identity, relevant metadata, missing analytics vs zero and unchanged export bytes on package-only edits. |
| M6.12 Media-resource feasibility assessment | Ready for Review | `FLAMINGO_INTELLIGENCE_DESIGN.md`, `mangayomi.py`/providers | Primary repositories checked for named Mangayomi/NewPipe/Stremio. Local integration is index listing plus fixed-domain generic provider, not all upstream extension execution; upstream index repo archived. No video supplied: exact video contents/versions unassessed. No extension installed or streaming/download integration proposed. |
| M6.13 Existing source-catalog hardening | Not Started | `mangayomi.py`, source catalog API | Repair unverified TLS contexts; distinguish live/cached/seeded fallback and stale/archived catalog provenance, validate catalog shape, preserve existing provider compatibility. Offline tests for certificate failure, malformed payload, cache/seed labeling and no remote extension execution; no unreviewed replacement fork. |
| M6.14 Current quota accounting | Not Started | `yt_api.py`, demand/SEO budget reporting/tests | Current official docs use a separate search bucket; existing single-counter/100-unit assumption differs. Verify actual project allocations before changing runtime defaults; support bounded per-bucket accounting and pagination/retry cost evidence. Fake-transport tests; no live quota/key/settings change. |
| M6.15 Connected research/series/packaging views | Not Started | existing Home/Library/Chapter/Queue/Activity | Research Desk investigations/evidence/trends, Series Room Bible/history/branding and Packaging Studio concept/claim previews extend current surfaces; no parallel queue/canon database. Tests deep-link shared IDs, dated evidence/disagreements, unavailable access and owner decisions preserved. |

**Evidence:** audit found lifetime-average thresholds and no repository vidIQ integration. The additional source/app feasibility check is documented separately; it is not implementation of the Investigator. Intended Flamingo channel authorization is still absent in the October 18 read-only connector check (0 reported credits); no account identifiers are committed in this assessment. **Risks:** private intended-channel data unavailable; no speculative scores. **Rollback:** additive observations and opt-in adapters.

## M7 Shorts Reels and community content

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M7.1 Approved-source content package | Not Started | new draft asset planner using shared Episode Intelligence/provenance | One approved parent episode/export yields coordinated but distinct mini-stories, character image/carousel/discussion/poll drafts, objective, evidence/continuity links and platform metadata; no merely cropped horizontal reposts. Test unsupported asset claims, stale parent versions and independent asset review/QA. |
| M7.2 Vertical layouts captions and QA | Not Started | existing renderer extensions, preview UI | Platform-appropriate hooks/crops/pacing/readability; deterministic synthetic previews and caption timing tests. |
| M7.3 Approval/publishing integration | Not Started | existing queue/approval controls | Independent asset QA/approval; no automatic real posts; mocked backend refusal tests. |

**Evidence:** transport exists; authoring absent. **Risks:** creative format standards and platform details require evaluation. **Rollback:** draft-only opt-in assets; original long-form unchanged.

## M8 Audience analytics and continuous learning

| Task | Status | Files | Acceptance and tests |
|---|---|---|---|
| M8.1 Authorized analytics ingestion | Blocked | analytics adapter, existing public stats | Intended-channel impressions/CTR/retention/watch time/subscriber/returning-viewer records; distinguish unavailable from zero; fixture tests. |
| M8.2 Comment and correction evidence | Not Started | new draft insights linked to existing validator feedback | Deidentified requests/complaints and personalized reply drafts; no unsolicited public messages; fake inputs. |
| M8.3 Unified briefing and learning | Not Started | existing Home/Activity/Chapter + Series/Research views | Audience Desk connects performance/experiments/community requests to episode/series and programming alternatives; trace creative recommendations to dated observations/approved corrections and feed research/production decisions. Test same-age cohorts, unavailable metrics vs zero, conflicting evidence, suggestions without automatic canon changes and no causal guarantees. |

**Evidence:** public views/likes exist; private feedback loop absent. **Risks:** account authorization and privacy; avoid public commit of private analytics. **Rollback:** additive read-only/draft integrations.

## Checkpoint log

- Owner typed “launch”: approved $0 local staging now runs exact candidate `9abeb62` at `http://localhost:3138` with isolated backend8138. Browser signed in; live form/session/proxy/API isolation checks pass, browser warnings/errors empty, publishing/OAuth blocked403, scheduler off and cap$0. No Chapter34 processing/source copy, paid calls, cloud provisioning or production changes. Queue simulation, semantic coverage, legacy adoption and real-media calibration remain incomplete. See Chapter34 test report for current state and legacy empty-workspace/API cookie-host observations.

- Chapter 34 staging request: integrated M1–M3/hardening regression snapshot passes 104 tests; stacked PRs #1–#5 verified draft/unmerged. Added offline staging preparation and 20 boundary regressions, separate storage/auth, fail-closed proxy and disabled publishing/account/automatic jobs. See `FLAMINGO_CH34_STAGING_TEST.md` for the concrete $0 local launch proposal and stage evidence protocol. **Not deployed; Chapter 34 source/original project unverified; no chapter processing or paid calls.** Launch/spending approvals, source access, labeled queue simulation and actual renderer/media validation remain outstanding. M3 semantic coverage, legacy adoption and real-media calibration remain incomplete.

- October 18: owner authorization recorded; roadmap/vision created; development-only Vercel Git deployment guards and persistent agent instructions prepared. Next: M1 shared resolver and decoded diagnostics. All production behavior remains unchanged.

- M1 offline checkpoint: resolver, diagnostics, export manifests, review/readiness contracts and UI implemented. 40 combined pytest tests and 421 selected existing script checks pass; tsc/build and isolated browser flow verified. Production enforcement remains OFF. Next: M2 dialogue/provenance and critique reliability.

- M2 checkpoint: F1/F2/F5/F7 repaired offline; M2 Ready for Review. Source references mean available evidence, not proven semantic coverage. Next safe work: M3 content fingerprints with explicit legacy-cache handling and editorial preservation.

- M3 review checkpoint: content-aware generation caches, staged/preserved sources, archival reruns and deterministic source linkage implemented/tested. M3 stays In Progress: semantic coverage and reviewed legacy adoption remain open. Next independent engineering task after the new architecture plan: read-only Series/Episode projection fixtures; legacy adoption still needs reviewed artifact/attestation decisions; real-media calibration remains an owner-evaluated release prerequisite.

- TV-production addendum checkpoint: vision and eight-milestone backlog extended; connected Series/Episode Intelligence and all ten consumers, external evidence/opportunity contract, selective invalidation, truthful packaging and existing-page mapping documented. Primary-source app/access review complete; actual video unassessed because no video/link was supplied. New repair findings: archived upstream Mangayomi catalog/unverified TLS/fallback provenance and current YouTube quota-accounting mismatch. No new engine, collector, integration or live setting implemented. M3 safe cache/linkage hardening is Ready for Review; semantic/adoption/calibration evidence remains incomplete. Next independent engineering task: Unicode-safe identity lookup and read-only shared-record projection/schema fixtures before dependent live research features.
