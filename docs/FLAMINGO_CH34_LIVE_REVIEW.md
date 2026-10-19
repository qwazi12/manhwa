# Chapter 34: live release, end-to-end test and editorial review

October 18, 2026. The owner authorized merging and deployment followed by testing in the current Next studio. The original Chapter 34, its cancelled variant and the four historical Waiting jobs remain protected. Nothing is authorized for publication, scheduling or external upload.

## Delivered release

[PR 10](https://github.com/qwazi12/manhwa/pull/10) merged once at expected head `635645964a2f70597f3231e26ea0ba3dcb06150b`. Production release: `5fec5c7c7b3fb5611f1f925b2a4289aed09f6be6`.

- Railway deployment `a07baa62-2277-4f0f-a09d-b88b94a5e02c`: SUCCESS, Online 1/1, no critical alerts or warnings, backend health HTTP 200.
- Vercel deployment `dpl_2uysdSFBRdPrqC4i9HDco3f9WBwd`: Ready / Production, exact commit verified, canonical alias `manhwa.nodepilot.dev`. Settings visibly reports deploy `5fec5c7`.
- Validation: **262 Python tests**, **419 checks across 12 relevant script suites**, **28 isolated-runner regressions**, TypeScript and a Next production build passed. Expanded export tests first exposed an empty-URL placeholder-card bug; it was fixed, the six export-swap checks passed, and the integrated Python suite passed again. Initial failures remain private evidence.

The release repairs AAC assembly, excludes only intentionally omitted credits/fragments from missing-description requirements, and adds a tested QA-only project policy. The synthetic 60-shot regression verifies every soundtrack boundary, zero video start, immutable inputs, unsupported-stream refusal and preservation of the prior output on failure.

## What the earlier failure actually meant

The earlier local MP4 repeated AAC encoder priming/padding across independently encoded shots. Near the end, the soundtrack and expected voice schedule differed by about 2.1 seconds. This produced one duration failure and 74 correspondence findings; it did not mean 74 source voices were absent. All 76 voice files existed. Full decoding passed, but timing did not.

The deployed assembler copies encoded pictures, decodes each shot soundtrack independently to its exact video-frame span, and encodes the final AAC track once. It fixes the measured cause rather than suppressing warnings or relaxing thresholds. The original local MP4 and a 321-file snapshot remain preserved privately.

“Diagnostic mode” explains whether findings block approval; it is not a quality verdict. Signal checks do not establish correct spoken words. Semantic speech QA remains unrun, real-media thresholds remain uncalibrated and global `MEDIA_QA_ENFORCE` remains 0.

## Live test and isolation

[Open the new Chapter 34 test in the current studio](https://manhwa.nodepilot.dev/chapter/a-regressors-tale-of-cultivation_34-liveqa-1018).

Only project `a-regressors-tale-of-cultivation_34-liveqa-1018` was created. Ingest job: `1d7130aa8d65`. Render queue item: `3ef35e35e6`; render job: `056807065695`. No old job was resumed. The idle local test runner was stopped and its browser tab closed; its files remain available.

The `.flamingo-qa-only` marker existed before ingestion. The studio visibly labels this test. An actual live approval probe returned HTTP 403 before a review record could be created. The policy blocks approval, posting-queue admission, publishing readiness and both Drive worker paths, independently of diagnostic media enforcement. Production accounts, destinations and automation settings were not changed.

The test uses a project-specific Charon narrator with restrained dramatic direction. Its approved estimated generation allowance is $5, monitored against a production baseline of $0.406672. The production daily cap remains $7. Recorded new generation and packaging increment: **$1.0151**; the prior isolated test cost approximately $0.93. These are estimates rather than provider-invoice totals.

## Stage evidence

| Stage | Observed result | Limits or findings |
|---|---|---|
| Ingestion | 21 source pages downloaded from the supplied Chapter 34 URL | Aggregator numbering has not been proven equivalent to the official publisher's paid-preview numbering |
| Panels / descriptions | 95 records: 78 art, 8 bubble, 3 credits, 6 fragment | One additional thin decorative border was misclassified as art; see image finding below |
| OCR / dialogue | Typed source text and source-line references are visible with storyboard beats | Available references are not proof every source event was understood or covered |
| Series context | Actual project Bible contains 9 existing cast records | Four existing name disputes and future novel information require review |
| Script | 1,581 words; valid model review completed with 0 issues / 0 rewrites | Human inspection found repeated title, name inconsistency and repetitive action prose |
| Narration | 78 Charon voice lines prepared | Restrained direction lengthened the raw 769.5-second schedule; performance and delivery need direct media review |
| Matching | 80 segments, source-linked beat/panel/text evidence | 12 raw holds exceed 12 seconds; maximum 16.954 seconds |
| Render / MP4 | Complete: 228,109,843 bytes, 1920×1080, 30 fps, H.264/AAC; player advances without error | Actual duration 703.700 seconds (11:43), not the raw storyboard runtime shown elsewhere |
| SEO / thumbnail | Four title suggestions, description/tags and five thumbnail concepts prepared; selected 1280×720 JPEG | Inherited metadata was wrong for this chapter; corrected only this test export, preserving automatic output privately |

Two current storyboard findings remain unsuppressed: a high-severity image finding on `page011_panel_001`, and a medium pacing finding for two beats sharing `page010_panel_006` for 16.17 seconds. Direct inspection confirms the first is a 437×69 decorative hatching border, not a meaningful scene. It is incorrectly used under “Ash drifted across the freezing courtyard, leaving Eunhyun alone to anchor the line.” The validator is correctly exposing an upstream classification/matching problem.

## Candid story and episode critique

**The observed script and board do not meet the requested television-production standard.** A functional rendered comic recap and a valid automated critique are useful, but they do not establish a well-directed episode.

The live opening repeats effectively the same series name as its own alternate title. It spends many beats on interchangeable blood, blasts, gore and frightened reactions before explaining who is protecting whom. It changes from Makli Hwangshin to Makli Wangshin in the middle and later changes back. The writer loaded a Bible containing a different spelling from this translation; shared context loading alone did not enforce consistent terminology.

The dramatic spine is present: possession after apparent defeat, a weakened body with an experienced enemy's dangerous power, Young-hoon needing evacuation, Eunhyun refusing to retreat, the twenty-second sword form cutting the attack, and a barrier closing the escape route. That is the episode the editing should foreground. The present cut delays the rescue stakes, repeats spectacle and gives too many panels similar dramatic emphasis.

Raw board landmarks (before final playback-speed adjustment): possession recognition about 3:23; fifth-star vessel limitation about 4:29; thirteenth-star threat about 5:47; refusing to abandon the evacuation about 9:31; sword-form response about 9:53; barrier realization about 11:30. These are storyboard times, not asserted final MP4 timestamps.

A better revision should name the immediate situation concisely, establish the wounded-ally dilemma early, compress repeated blasts, retain the distinction between the fifth-star vessel and the enemy's effective strength, allow the successful counterattack to land, and make the barrier a clear change in the escape plan. It must not invent victory or later-chapter outcomes. Scene-aware framing, pronunciation review and intentional pacing remain necessary. The original 8/8 clean automated critique did not catch these structural shortcomings.

## Research: what existed, what was used, what remains uncertain

The earlier local test had no Bible loaded. Its writer used chapter descriptions/OCR; SEO was prepared afterward. That SEO returned 22 grounding citation entries, but redirects were blocked/unverified, and it measured no channel uploads or comparable videos. That was not a validated audience brief.

Production has more infrastructure. Its existing series pack contains 25 stored citation entries and was updated October 11 at 20:53 Eastern. Queries covered title/author/publisher listings, Korean title, MangaUpdates, fandom characters and YouTube recaps. Alias-keyed SEO caches contain 23 and 27 entries. Citation counts are not unique verified facts or a measure of audience demand. The current live project demonstrably loaded all 9 cast records. Some Bible roles and titles refer to future novel developments, so episode cutoff and provenance review remain essential.

The current Library card reports **medium YouTube demand: 348 median views/day across 11 matching recaps, 5 published within 90 days**. Code inspection shows this is the median of lifetime views divided by video age, with a minimum age of one day and a capped 15-result title/alias-matched search. It is not measured current daily velocity, total market demand, retention, CTR or a forecast for this video. Activity records the earlier 14:30 research pass over 15 series using 1,515 YouTube quota units. No new paid vidIQ call or broad external integration was used during this repair.

A targeted official check during this repair opened [Naver's series listing](https://m.comic.naver.com/webtoon/list?titleId=839827). It lists 회귀수선전, creators 김무현 / 엄청난 and a martial-arts/historical category. The current Chapter 34 entry is paid preview; it was not purchased or opened. This check was not part of the original writer's evidence and does not validate aggregator episode equivalence or all story claims.

The External Research Investigator and shared verified Series/Episode Intelligence architecture remain planned work. Existing Bible, research, demand and SEO stores should be connected through reviewed identity, episode events, dates, evidence and uncertainty; they should not be replaced with disconnected generators or duplicate databases.

## Packaging review

The earlier local title “ANCIENT MONSTER Awakens” was generic; “Wipes Them Out” overpromised the episode outcome. Its chapter identity leaked the internal test variant into title/description/badge. The description mostly summarized the opening. Generic extracted tokens such as Refining/Core and unsupported dungeon/system genres weakened the tags. A heuristic 80/100 score is not observed CTR or verified demand.

The earlier selected thumbnail used a surprised bystander, cropped the upper face and crowded the lower face. Its explanation called the face the main character despite `face_is_mc=false`. Five concepts and a compliant 1280×720 JPEG did not establish a strong thumbnail.

A grounded direction is a title about refusing to retreat while a wounded ally cannot escape, paired with the actual swordsman, descending attack or closing barrier. Short complementary text such as “NO ESCAPE” should clarify the dilemma rather than invent an outcome. Use Chapter 34 without the internal test suffix. The description should establish the rescue, opponent and trap; tags should use specific series aliases, relevant confirmed characters and cultivation/martial-arts terms. Global owner-approved series templates and hooks are not being silently changed.

The actual live automatic payload inherited the old series title hook and a Chapter 32–33 description, tags and timestamps. Its new suggestions focused on the opening blood-vortex scene rather than the whole rescue/escape dilemma. The selected cover thumbnail was clear but generic and displayed the internal `liveqa-1018` suffix. The chapter-card alternative cut off the focal face and placed a large badge over it; it was not selected. This is a metadata inheritance and identity problem, not evidence of an episode-aware research plan.

Preserved the complete automatic payload and concepts under this new project's private `owner_review/automatic-packaging/`. Corrected only this export:

- **Title:** He Must Hold the Line | A Regressor's Tale of Cultivation Chapter 34
- **Description:** the possession, wounded Young-hoon, evacuation, Eunhyun holding the line, sword response and sealing barrier; no invented victory, Chapter 32–33 timestamps or test-suffix text.
- **Tags:** specific series variants/Korean title, Chapter 34, Seo Eunhyun, Kim Young-hoon, cultivation, murim, martial arts and manhwa recap. Omitted disputed villain spelling and generic rankings.
- **Thumbnail:** retained the owner's existing cover composition and corrected this concept's badge to CH 34. JPEG 1280×720, 136,995 bytes, no thumbnail-store advisories. This is a clean preview asset, not a validated best-performing episode thumbnail.
- **Test metadata:** private, no publishing targets, no publish time; the QA-only policy remains in force. No series defaults, approved hook/style, connected account or destination changed.

The current studio displays the corrected fields. The MP4 and thumbnail downloaded successfully. A browser attempt to download the optional upload-package ZIP timed out; ZIP delivery is not claimed verified. No upload was initiated. Title `68/100` is the character counter, while the separate SEO confidence label is a heuristic, not measured CTR.

## Protection and operational checks

Immediately before and after the release, all 9 protected job files, all 153 original/cancelled Chapter 34 files and all 3 queue/settings files retained exact hashes. No work was active at rollout. The normal render queue added one new test entry. Its 40-finished-item retention policy then pruned the oldest completed row, `d7aa2e2454` for I Am the Fated Villain Chapter 361. The final audit caught this. After verifying no active work and that all other old rows were identical, saved the completed QA queue item and post-test queue snapshot under the new project's `owner_review/`, then atomically restored the exact pre-test queue bytes. No job was cancelled or retried. The completed QA video and durable job evidence remain intact. This incident is recorded rather than described as uninterrupted queue preservation. QA render history should be isolated from ordinary retention in a future regression-backed fix.

The four historical Waiting IDs remain `684b271aa3b2`, `97b762094d26`, `e7deae78a155` and `ea244d5ffbb8`: two completed Dark Mage Chapter 105 finalization attempts, a completed Murim Chapter 45 finalization attempt and its historical publishing confirmation. Their historical outcomes were verified under the owner's status exception; labels/files were not rewritten. Cancelled Chapter 34 job `c5b1067541f1` remains cancelled.

The live SQLite database and compressed startup backup passed integrity/FK checks and an isolated temporary restore with 72 projects. Raw restored database SHA: `314cf3dda3a9f5436c2b650e3362dee4f29b061301aa56966a5060824111d626`. Railway's 18:08 Eastern full-volume restore point is available (17.54 GB). A full-volume restore drill and automatic volume-backup schedule remain incomplete; the enabled SQLite backup schedule is separate.

Read-only current-studio smoke loaded Library (15 series), Activity, posting Queue and Settings. Queue showed 2 Needs review, 0 Ready, 19 Posted, 0 Errors; these filtered UI counts differ from raw retained historical records. Existing automation remains as configured. Destructive operations and actual publishing were not exercised live.

## Review-method boundary

The complete live storyboard text and source-linked descriptions were read. The actual live MP4 was downloaded and probed independently: video 703.700 seconds, audio 703.690 seconds, both start at zero. MP4 SHA-256: `bb73f565a9eb7425cf74984faa9e8dc21b55d5cdc695730ff0b57bdc0e510098`. Live player ready state 4, no media error, playback advanced from 0 to 10.97 seconds.

**Final-media QA: passed, fresh report, full video decode passed, zero critical findings, one pause warning** at approximately 10:34 (1.04 seconds inside one voice file before playback-speed adjustment). The previous cumulative duration/correspondence failures did not recur. A clean signal result does not establish semantic correctness or creative quality.

Inspected 78 representative frames sampled every nine seconds across the delivered video, the actual thumbnail and the flagged source border. The border is visibly on screen around 5:46. Tall panels commonly occupy only a narrow center column against blurred background; repeated motion and extended spectacle weaken pacing. Whole-track local ASR completed in 96.6 seconds using a cached model, producing 131 transcript segments without external calls or a model download. Read the entire transcript. It includes the core narrative and the inconsistent name forms; some phrases are missed or misrecognized, so it is not a word-perfect semantic pass.

Approximate delivered landmarks from ASR: possession explanation 3:13, fifth-star vessel 4:16, thirteenth-star threat 5:21, explicit wounded-ally evacuation dilemma 8:53, twenty-second sword form 9:02, sealing barrier 10:35. This confirms that the cut waits too long to foreground the rescue stakes.

Three audio samples were extracted, but the current tool could not present audio input to the reviewer. **Direct listening, vocal tone and pronunciation are therefore not verified**; the review uses frames, script, signal analysis and whole-track transcription. No full real-time manual listen is claimed. The earlier local MP4 was checked with whole-track offline ASR using a cached model: 1,404 of 1,479 normalized reference words matched in order (94.9%). Proper-name/phonetic differences are not proof of mispronunciation, and ASR is not ground truth. No claim of a full manual real-time listen is made.

## Remaining work

The live media test and documented visual/text review are complete; the episode remains an editorial revision candidate. Before another paid regeneration, repair episode identity/metadata inheritance and QA history retention with offline regressions, then address the decorative-panel selection, duplicate spoken alias, name consistency and story structure. Direct voice-performance review still needs an audible review environment. Introduce shared reviewed event contracts and improve the editorial plan. Semantic coverage, reviewed legacy-cache adoption and real-media calibration remain explicitly incomplete. None of those capabilities is established by this single test.

## Why the current studio still shows other findings

The board's two G0 missing-audio errors refer to segments 39 and 79, both explicitly `user_included=false`; they were not in the final video. All 78 included clips rendered and no included segment is silent. The selected-export media QA passes. A separate pre-render diagnostic is still validating excluded placeholders, and the pipeline strip reports 78 of 80 as “part done.” These are misleading selection-aware status calculations, not evidence of two missing delivered voices. They need regression-backed repair rather than global warning suppression.

The runtime displayed in the chapter summary (12:47 / 767 seconds) is the raw included storyboard schedule; the actual 1.1× MP4 is 11:43. The page count 22 includes the fetched cover alongside 21 source pages. These distinctions should be made explicit in the UI.

Final post-restoration gate: 9/9 protected job files, 153/153 original/cancelled Chapter 34 files and 3/3 settings/queue files match baseline hashes; no active jobs, no running workers, same four Waiting IDs, cancelled job still cancelled, and no QA Drive record. Final evidence is saved privately in the QA project.
