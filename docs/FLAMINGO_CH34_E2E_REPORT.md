# Chapter 34 isolated end-to-end test — October 18, 2026

## Execution status

**The real isolated test produced a playable 1080p MP4 and completed generation/rendering. Final-media QA FAILED: an actual cumulative audio timing discrepancy requires repair before production use.** SEO and thumbnail preparation have completed. The video is available for owner review, with findings visible and no approval or publishing action.

The owner requested a built test to personally inspect. After a premature audit-only stop, a generation-only isolated studio was implemented, tested and launched. This uses the current Next.js interface and actual Flamingo pipeline, not the legacy UI or mocked generation responses.

- Studio: http://localhost:3138/chapter/a-regressors-tale-of-cultivation_34-e2e-1018
- New project: `a-regressors-tale-of-cultivation_34-e2e-1018`.
- New ingest job: `66e59324f415`, completed; render job: `31574612d58e`.
- Private source/data/media/evidence: repository `work/flamingo-real-ch34-run/`, excluded from Git.
- Production application: PR9 merged once at `91bfd89515b41aae445ceadda7d8f57bc68282f3`; Railway deployment `f13c028b-57b2-4c5e-a976-7c7f82e8105a` SUCCESS, current Vercel studio `dpl_5dyc8Tz2yZWGnDWFKGM8zG3jjMVX` Ready/Production, canonical alias retained.
- Separate isolation implementation: [PR10](https://github.com/qwazi12/manhwa/pull/10), review branch only; not deployed to production. Core ingest/server/description/narration/TTS/matching/render/media-QA bytes match the released PR9 application. Test boundaries and visible banner are additional wrapper behavior.

## Release and preservation gates

Before merge,245Python tests passed again; the unchanged candidate also has189existing script checks and TypeScript/Next production build passes. CodeRabbit success and exact expected PR head were verified. A new Railway full-volume restore point is available at **18:08 Eastern,17.54GB**, retaining the earlier restore points. Current populated SQLite and its compressed backup both pass integrity/FK checks and isolated temporary restore with72projects and matching populated rawSHA.

Production JobsBar/running lists were empty; posting raw queue contained32removed/20posted and no active processing, render queue39done/1error and no waiting/rendering. The four confirmed historical Waiting records were retained under the owner's explicit exception. Both immediately before and after deployment, all **9protected job files,153original/cancelled Chapter34files and3queue/settings files** matched their stored SHA256baselines. Original Chapter34 and the cancelled upd-1018variant were not resumed, regenerated or edited. The deployed cancelled-variant page now opens and explicitly says Stopped by you.

The new test is stored outside production and does not contain those production job records. A controlled restart of only the isolated services after ingestion preserved **209test project/job files** byte-for-byte; no generation was rerun.

## Isolation implemented and verified

- Separate checkout, project/media tree, database, HOME, caches, usage accounting and fresh authentication; loopback-only frontend/backend. No production volume or shared mutable project directory.
- Only existing Gemini/TTS generation credentials may enter the backend. Publishing/social/Drive/account credentials are excluded. The frontend and proxy receive no generation credentials.
- macOS sandbox denies all writes outside the private test directory and denies direct external network access for the backend, frontend and their descendants. Local ephemeral ports are allowed for browser asset/debug servers; the Python worker guard restricts connections to the test studio/proxy ports.
- A CONNECT-only proxy permits required source/provider hosts plus the public pinned JavaScript asset host needed by the renderer: Asura/CDN, Google generation/TTS and jsDelivr. TLS remains end-to-end. Logs contain hosts/allow decisions only, never secrets, headers or query strings.
- Legacy scraper requests for an unverified TLS context are overridden in this test profile to require certificate validation. Actual source download passes with verified TLS and the existing scraper user agent.
- HTTP publication/account/Drive/automatic-job/review-approval/schedule routes return403. Actual export-worker Drive copy, scheduling and automatic recovery are also blocked. Cleanup is disabled in the test process so evidence is retained. Production settings remain unchanged.
-28isolation regressions pass, including forbidden HTTP not reaching the application, forbidden proxy destinations never being dialled, actual `_after_export` preserving QA/packaging without upload, credential/path refusal, inherited TLS/network guards, and real OS subprocess refusal to write an outside sentinel or connect directly outward.
- Live API checks confirm publishing, Drive and scheduling403, scheduler/autopilot paused, no old jobs, posting off, authenticated current-UI proxy works. New test estimated-spend limit is$5; this is the existing application estimate guard, not a promise about provider billing for failed requests.

## Stage evidence

| Stage | Actual result | Inspection / boundary |
|---|---|---|
| Source ingestion |21pages, no scraper count warning; fresh network download | Ordered source files hashed; no production source/cache copied |
| Panel extraction |93crops, background-registration engine |5text slivers merged;6tall panels checked;20vision refinement cuts,0vision errors/capped |
| Descriptions and OCR |93records;76art,8bubble,3credits,6fragment; OCR text on57panels,141typed lines | OCR is the actual vision-description path, not a separate Tesseract run; source text is evidence, not proof of perfect transcription/speaker attribution |
| Dialogue transfer |8transfers,0recorded orphans | Direct quotation mode remains off; the narrative paraphrases rather than inserting unapproved quotes |
| Script |1,444words across8units |8/8review units submitted; valid response, outcome clean,0reported issues/revisions; manual findings below still apply |
| Narration |76new voice files, Gemini Charon/energetic style | All76timeline beats have corresponding files; no legacy voice copied |
| Matching and framing |Gemini embeddings + provenance + DP; precise shot crops | No embedding fallback; all76segments included; structural timeline0errors/0repairs |
| Timeline |501.6seconds before export speed adjustment | Longest aggregate panel hold10.45s; no missing voice paths; source linkage remains semantic-not-assessed |
| Render |76/76clips, intro/outro, normal export;220seconds | H.264 High Level4.1,yuv420p,1920×1080,30fps;AAC48kHzstereo;177,936,328bytes |
| Final-media QA | Full video decode passed; overall FAILED with75critical findings |1duration discrepancy +74voice correspondence warnings; semantic speech not run, thresholds uncalibrated, enforcement off |
| SEO and thumbnails |4title suggestions, description/tags and5thumbnail concepts; chosen1280×720JPEG111,878bytes | No connected channel/competitor measurements; draft metadata remains unapproved |

## Failures found and resolved while building the test

1. The initial offline profile could not do real generation. Added a separate profile rather than removing its safety guard.
2. A bare source request without the scraper browser user agent returned403. The actual scraper downloads all21pages. Strict TLS was then enforced and the same download passed.
3. A renderer smoke test could not locate Chrome under the new HOME and attempted a disallowed download. Reused already-installed public pinned browser/font assets in the isolated cache; no browser download or personal browser profile was imported.
4. The next renderer smoke reached every frame but refused a missing GSAP script blocked by egress policy. Browser requests now go through the restricted proxy, with its required public script host allowed; local browser asset/debug ports are permitted while direct external egress remains denied.
5. Final sandbox renderer smoke passes **14/14**, including minimum every-frame SSIM0.955, stream/timing/audio/silence/crop behavior and overflow refusal. Initial failures remain in private logs, not rewritten as passes.

## Editorial and QA findings requiring attention

- **Opening does not name the series or establish its context.** The first line begins immediately with the blood explosion. This fresh isolated series has no production series pack, exposing an onboarding/default-opening gap; do not silently copy the original package and claim fresh behavior.
- **Early narration is repetitive and adjective-heavy.** Many early beats separately describe crimson energy, gore, explosions and screaming. A cleaner episode opening should establish who is threatened and compress repeated action while preserving causal information.
- **The model's clean critique is not a creative-quality guarantee.** It returned a valid clean response for all8units, yet the preceding editorial observations still stand. Semantic completeness, truth of inference and pronunciation require human/source evaluation.
- **Five high-severity rule findings are misleading in this sample.** They flag missing descriptions for page002_panel_002,page019_panel_002,page021_panel_002/003/004. Inspection shows credits/thin fragments; all five are classified fragment and excluded from the video. No Accept or repair action was applied to suppress them. The validator should account for intentionally excluded non-story panels rather than label them Definitely wrong.
- **Technical pacing is improved relative to the preserved original:** no missing narration paths and the longest panel hold is10.45s, compared with the original's excluded missing-audio slots and ~42srepeated-panel hold. This is a measured comparison, not a claim that the new editing is artistically optimal.
- Source reference coverage remains candidate evidence; a complete semantic coverage verdict and full manual source-to-script audit have not been established. Media thresholds are uncalibrated and production enforcement remains off.

## Final delivery measurements

- File: `final_Oct18_06.26PM_1.1x.mp4`;177,936,328bytes (177.9MB decimal).
- SHA256: `a7fb45e54e7f9fb50697c357b480f6f9d74880965780c78796c2259b7a69e378`.
- Video duration462.633333s; audio464.834s; player/container duration464.834s (7:44.834). Render manifest expects462.634s. Decoded audio duration464.853s. This is a real discrepancy, not a QA pass.
- Current Next studio player loaded readyState4 and advanced from0 to52.2975seconds without media error. Intro, representative middle frames and outro were inspected from the actual export. The blurred-background/panel presentation and cards are present.16second opening/middle/ending soundtrack samples were extracted for inspection; a complete listening/transcription audit has not been performed.
- Additional read-only signal diagnostic tested beats0,2,38,74,75. Best audio lags grow from0 to0.06,1.08,2.12,2.14seconds; shifting those five comparisons gives99.3–100% spectral support. This supports cumulative timing drift as an explanation for many correspondence failures; it does not prove all speech is correct or authorize lowering thresholds. Actual missing words and full audio/video alignment still need evaluation. Private `alignment-diagnostic.json` retains values.
- Do not approve this output as release-ready. Preserve it as the original observed test, then repair concat/timing mapping with real-media regression coverage in a separate reviewable change; retain QA failures until corrected and verified.
- Browser UI also reports a raw502second runtime instead of delivered465seconds, and counts22pages after adding a cover although there are21chapter source pages. Those display issues need clear source/cover/runtime semantics.
- Automated opening narration lacks a series introduction, while the intro title card does identify the series. These are distinct observations.

## Packaging inspection

The chosen automatic title is “ANCIENT MONSTER Awakens! - A Regressor's Tale of Cultivation Chapter 34 (e2e-1018) Manhwa Recap”. The hook refers to the depicted awakening, but the test suffix leaks into public-facing metadata and should be separate from chapter identity. Description covers the opening blood eruption/court threat rather than the whole episode arc. Extracted characters include generic terms such as Refining and Core. Suggested genres include unsupported dungeon/system categories. The alternative Wipes Them Out overstates what has been verified; do not select it without evidence.

Existing SEO invoked its existing Google grounding path through the allowed generation provider, returning22citation entries; client resolution of grounding redirect URLs was blocked by isolation. Zero channel uploads/comparable videos were measured (YouTube API credentials absent). Its80/100high-confidence label is heuristic, not verified audience demand, factual correctness or expected CTR. The claimed publisher/platform and external facts remain unverified in this test; do not treat returned citations as checked source evidence. No new broad research integration was built or enabled.

No publication, schedule approval, account change or Drive upload occurred. Final production preservation recheck after playback:9/9jobs,153/153original/cancelled Chapter34files and3/3queue/settings files unchanged; jobsbar empty,running0,same four Waiting IDs,cancelled variant still cancelled,productionSHA91bfd89.

## What worked, failed and comes next

**Worked:** gated PR9release/backups; isolated authenticated current studio; fresh source through narrative/voice/matching/full render; linked source evidence; actual final decode; diagnostics visible beside playback; unchanged protected production data.

**Failed / needs repair:** cumulative audio timing discrepancy, noisy media warnings needing diagnosis rather than suppression, excluded-fragment validator warnings, repetitive/context-poor opening, draft metadata identity leakage and uncertain factual grounding, misleading raw runtime/source-cover count.

**Still unfinished architecture/evaluation:** semantic story coverage, legacy cache adoption, approved real-media calibration and full source-to-script/pronunciation review; shared verified Series/Episode Intelligence and broad Research Investigator remain roadmap work. No full-volume restore drill or automatic volume schedule was performed. Next independent safe task: preserve this media as a regression fixture privately and isolate the concat/audio timing cause with tests before proposing/deploying any repair.


### Completed thumbnail and owner handoff

Preparation completed without a duplicate request. Five local thumbnail concepts were built after61cached face-analysis entries. The selected Panel hero JPEG is1280×720,111,878bytes and passes file constraints. It displays ANCIENT MONSTER and CH34(e2e-1018), with a surprised witness rather than the actual monster as its focal image. The lettering is legible at full size, but the test suffix, loose hook/image connection, cut-off upper face and crowded lower face require editorial revision. The series style remains a draft, not owner-approved. The title-alignment heuristic itself reports a loose link; do not interpret medium69confidence as measured click performance. No thumbnail upload to a connected service occurred; the internal uploaded_at field records a local save.

The owner-facing studio is left signed in on Watch the video, paused at52.319seconds after verified playback, with the MP4, source/board/Issues, SEO and all5thumbnail concepts available. All generation/render/packaging jobs completed; estimated total shown by the test studio is$0.93of its separate$5limit. This is estimated application accounting, not provider invoice reconciliation. OS isolation, denied publication/Drive/scheduling routes and the separate loopback-only services remain running for review.
