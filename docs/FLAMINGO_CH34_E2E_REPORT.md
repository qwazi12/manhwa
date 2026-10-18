# Chapter 34 isolated end-to-end test — October 18, 2026

## Execution status

**Real ingestion, OCR/descriptions, scripting, narration, matching and timeline have completed. Full rendering/QA/packaging are in progress; no completed MP4 claim yet.** This report is updated with final measurements when those stages finish.

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
| Render |76clips requested through the normal isolated render queue, keep_voice=true | Actual export/QA/packaging follows; final measurements pending |
| Final-media QA |Pending full MP4 | Must inspect final decoded streams/schedule/voice support, not substitute JSON validity |
| SEO and thumbnails |Pending export preparation | Current local packaging enabled; external publishing/account/YouTube data paths disabled |

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

Pending rendering, final QA, packaging inspection and browser playback. No publication, schedule approval or Drive upload is authorized or enabled by this test.
