# Flamingo Recap Studio — overall status

October 18, 2026. Production is the current Next.js site at https://manhwa.nodepilot.dev/ running **88920f3**. PRs1–8 are merged, with #7 evidence included through #8. The cancelled-chapter visibility repair described below is a separate **review-only feature branch**, not deployed. No work in this checkpoint resumes Chapter34 or changes the four historical Waiting records.

## Where the product stands

Flamingo has an operational production pipeline and creator studio: source ingestion, panel processing, OCR/descriptions, Bible-assisted writing, voice, matching, storyboard editing, rendering, packaging and approval/scheduling infrastructure already exist. M1/M2 and safe M3 repairs strengthen that pipeline and are deployed. The target of a coordinated television production company with one verified Series/Episode Intelligence layer is still partly architectural work. Current AI output needs editorial review; a source link or a passing structural test is not proof of story completeness or creative quality.

There is no defensible single completion percentage: the pipeline exists, operational safety has improved, and major intelligence/creative-evaluation capabilities remain unfinished.

## Already works / implemented capabilities

| Area | What the tool can do now | Verification boundary |
|---|---|---|
| Current studio | Home, Library, Chapter Board/Issues/Video, posting Queue, Activity, Settings, Deleted/restore, with explicit legacy access | Current Next site verified; Library has15trackedseries. Legacy is not the primary UI |
| Source production | Ingest series/chapter links, download pages, split panels, preserve source files, extract structured OCR/dialogue and panel descriptions | Source/provider availability varies; no claim that every source or every bubble is interpreted correctly |
| Story and continuity | Existing sourced research/Bible/series packs, canonical context in writing/revisions, typed ordered dialogue, speaker/line provenance, anchored scripts and critique states | Model claims can be wrong; final story coverage and real narrative quality remain uncalibrated |
| Narration | Script-to-voice, configurable narrator/style, content-keyed TTS reuse, beats/slices and timing | Existing voices work; deliberate voice direction, pronunciation and scene pacing need further work |
| Visual editing | Source-linked panel matching, storyboard inclusion/reordering, crops/timing, motion, undo, focused reruns and range/compilation workflows | Existing rendering pipeline preserved; scene-aware direction remains future work |
| Rendering and exports | Clip/final video assembly, loudness/speed, exact-export identity, range checks and final-media diagnostics | New media enforcement remains off until real-media calibration; no fresh Chapter34 MP4 exists |
| Packaging | Existing SEO/title research, series hooks, chapter metadata, thumbnail candidates/readability/style and owner edits | These are not yet all consumers of a shared verified episode-event contract; heuristic scores are not observed CTR |
| Distribution | Review/approve, render/post queues, scheduled publishing and connected video Drive copies | Existing connectivity/history verified; no new upload/publication or destination change in this checkpoint |
| Control and safety | Spend/call limits, cooperative stop/pause/resume, preserved edits, source/rerun archives, duplicate protection, job/activity visibility | Stop waits for an in-flight operation to finish; owner approval still governs live generation/release |
| Persistence | SQLite and database backups now on `/app/data/studio-ops`; verified populated snapshots and Railway volume restore points | Database isolated restore passes72projects; full-volume restore drill not performed |

## What was completed in this development sequence

- **M1:** shared audio resolution, decoded/final-media diagnostics, exact export/report identities, readiness contracts and Issues/Video display. Deployed diagnostically; enforcement remains off.
- **M2:** ordered typed OCR/dialogue transfer, explicit orphan text, truthful incomplete/failed critique states and preserved Bible/context during revisions. Deployed; creative quality still requires evaluation.
- **M3.1–M3.4:** content-aware generation identities, source/rerun preservation, cache freshness stops, incomplete-stage hardening and deterministic source evidence links. Deployed; references explicitly do not assert semantic completeness.
- **Architecture/vision:** existing research, Bible, SEO, thumbnail, demand and vidIQ-related capabilities audited. Detailed Series/Episode/evidence contracts, consumer mapping and reuse/repair/build backlog documented. This is a plan, not a completed external Investigator.
- **Release/staging safety:** isolated staging and regression infrastructure, QA deletion/restore race fix, populated ops-data persistence, startup ingest-scope fix and offline fixture repairs. PR8 is deployed through the normal main-only flow.
- **Operations:** Railway capacity alert cleared,50GBvolume, current21.08GBvolume restore points, populated72projectDB restored in isolation, protected files verified across the actual deployment. Existing thresholds/accounts unchanged.
- **This fix:** read durable ingest history for chapter context without repopulating worker memory. Partial cancelled chapters with an existing directory remain visible after restart; live state wins; render/research history is excluded; deleted directories are not recreated. The page explicitly says Stopped by you and adds no resume action for cancelled jobs. Separate feature PR awaiting review/deployment authorization.

## What still needs repair or validation

| Item | Current state / next evidence |
|---|---|
| Cancelled partial chapter displays unknown chapter after restart | Fixed offline in this review branch; still present on production until a separately approved release |
| Chapter34 original project | Preserved:21pages,95panels/descriptions,1591words,78voice lines,83segments,noexport. Evaluate five missing-audio findings on excluded slots66–70 and the ~42second repeated-panel hold without automatic regeneration |
| Fresh Chapter34 variant | Cancelled after21pages/93panels/93descriptions, estimated$0.407; no script/TTS/timeline/export. Remains stopped; monitor paused |
| Four historical Waiting labels | Later successful outcomes verified; records retained exactly under owner's exception. Any future reconciliation must be separately reviewable; no retry/cancel to clear labels |
| Series Bible identity | Confirmed Unicode/empty-alias lookup collision in isolated fixtures. Repair lookup before shared intelligence; preserve existing IDs/canon and avoid bulk re-keying |
| Semantic story coverage | M3.5 blocked on owner-reviewed labeled source/script examples and accepted omission/paraphrase standards. Reference coverage is not semantic coverage |
| Legacy-cache adoption | M3.6 blocked on representative bundles and explicit review/attestation policy; do not invent historical generation inputs or force paid refreshes |
| Real-media calibration | Need approved good/defective final-MP4 examples and narrative/creative evaluation; `MEDIA_QA_ENFORCE=0` |
| Backup operations | Existing database schedule is enabled; automatic Railway full-volume schedule is not configured. Full-volume restore exercise still needs an isolated recovery plan/window |
| Source catalog / demand accounting | Documented TLS/fallback-provenance hardening and YouTube quota-accounting verification remain planned; no live settings/access changes |
| Full fresh E2E validation | Chapter34 test stopped by owner. A complete source→script→voice→MP4 quality assessment has not been delivered. Normal render can auto-copy to Drive, so any no-upload test needs a verified path |

## What genuinely still needs building

1. **External Research Investigator:** discover promising series beyond the watchlist from official rankings/catalogs/releases, permitted community discussions, public YouTube competitors/breakouts, search interest, release timing and authorized vidIQ where available. Persist dated evidence, measured vs inferred interest, competition, uncertainty, audience fit and production feasibility. Broad integrations/paid credits remain approval-gated.
2. **Shared Series Intelligence:** reviewed stable identity/aliases, Bible continuity, terminology/pronunciation, source/release and branding/history views over existing stores, preserving owner edits and separating research hypotheses from canon.
3. **Shared Episode Intelligence:** versioned ordered source/OCR/speaker/event/scene evidence with uncertainty, corrections and Bible/source revisions used consistently by writer, narrator, editor, QA and packaging. No duplicate canon/project databases.
4. **Selective dependency handling:** show which outputs become stale after a source/story/voice/packaging change, preserving unaffected media and editorial decisions; current cache contracts are more conservative.
5. **Story-grounded packaging:** each title/thumbnail/description promise supported by verified episode events plus external audience evidence, opening fulfillment checks and observed experiment linkage. Extend existing SEO and thumbnail infrastructure.
6. **Creative direction:** narration rhythm/pronunciation and scene-aware image/crop/motion decisions tied to the same story evidence. Existing renderers remain the foundation.
7. **Original Shorts/Reels/community assets:** distinct short stories and platform-specific drafts, each linked to an approved parent episode and its own QA/approval.
8. **Audience-learning loop:** authorized private analytics and deidentified comment evidence connected to series/episode/packaging experiments, feeding proposals rather than automatically rewriting canon or posting replies.

## Current verification and protection

The new nine-case restart-visibility suite initially produced3failures/6passes, including the actual unknown-chapter404. After the fix, all9cases plus6startup-scope regressions pass. Final integrated result: **245Python tests pass**,58existing warnings; **189existing script checks pass** across chapter status31, job control41, sweep16, limits6, autopilot55, render queue15 and posting25; TypeScript and Next production build pass. Tests run with fake providers and denied external Python network in the isolated snapshot. No paid calls or media generation.

A fresh read-only production comparison confirms **9job files,153Chapter34files and3queue/settings files unchanged**, no running jobs, exactly the same4Waiting IDs and Chapter34cancelled. Production remains88920f3. No main merge, deployment, account/destination change or job-control action is part of this fix.

## Recommended order from here

1. Review this cancelled-visibility PR; release only with separate owner authorization and the same idle/backup/preservation gates.
2. Repair Unicode-safe Bible lookup and add read-only Series/Episode projection fixtures. These are independent offline tasks.
3. Obtain reviewed semantic/adoption/calibration examples and an approved isolated restore plan; continue preserving Chapter34 until explicitly resumed.
4. Implement shared evidence contracts and consumers before broad external research integrations. Add research adapters only with approved access/cost bounds.
5. Evaluate one complete episode against agreed creative/technical standards before wider automation, then packaging experiments, original shorts and audience learning.

Detailed evidence: [production verification](FLAMINGO_PRODUCTION_VERIFICATION.md), [roadmap](FLAMINGO_DEVELOPMENT_ROADMAP.md), [intelligence design](FLAMINGO_INTELLIGENCE_DESIGN.md), [vision](FLAMINGO_STUDIO_VISION.md), and append-only `manhwa-recap-v1/memory.md`.
