# Flamingo production verification — October 18, 2026

The completed M1–M3 implementation was already released under the earlier owner instruction. **No additional main merge or deployment is approved by the current verification gates.** A newly reproduced QA/deletion race is fixed and tested on `dev/flamingo-release-safety`, pending review and safe deployment conditions.

## Release and PR inventory

Production website: https://manhwa.nodepilot.dev/ (the current Next.js studio).

| PR | Contents | Verified disposition / head SHA |
|---|---|---|
| #1 | M1 final-media diagnostics and exact-export readiness | Merged; `2d4b53c5a3031c3c98e6dcb95c4bf204bdfb8076` |
| #2 | M2 ordered dialogue/provenance and narrative review | Merged; `a26a92f6523b7aa5eb5a8af2486e19f993b815fb` |
| #3 | M3 content-aware caches and source preservation | Merged; `2dea8be0db53f9046a12803687a843e9015d3afc` |
| #4 | M3 incomplete-description and freshness hardening | Merged; `7c2402b4683f8dff0fe09f3a07c4e242ba908653` |
| #5 | Research/shared-intelligence architecture **documentation** | Merged; `fc32713268c312bf248748753bff9468cf239bfa`; no Investigator implemented |
| #6 | Integrated stack and isolated staging tools | Merged once into main as `48a1eff1cacad241c35f87b07721a04adad11a79` |
| #7 | Earlier release/run evidence | Draft on `dev/flamingo-release-followup`; deferred to avoid a redundant production restart |
| #8 | QA deletion race and isolated test fixtures | Draft, `dev/flamingo-release-safety`, stacked on #7; tested code commit `b48b721`; not deployed |

GitHub confirms PRs #1–#6 closed/merged. The integrated #6 merge retained the stacked commits once; no force push or production history rewrite. Main remains `48a1eff`. The previous main rollback reference is `40d67cd`; no rollback was required or executed.

## Fresh validation

Tests ran in a fresh archive of deployed `48a1eff`, with the follow-up QA/test changes copied into that isolated snapshot for final validation. It contains no production projects, provider/account credentials or local `.env`; Python external networking is denied, and mutable storage is separate. Mock metering uses a fixture-only allowance; production spending limits are untouched.

| Check | Actual result |
|---|---|
| Broad Python tests, unmodified deployed code after correcting harness auth | 208 passed, 6 failed, 58 warnings; six stale Series Bible expectations |
| QA deletion regression before fix | 1 failed: a late checker recreated the deleted project folder |
| Final combined Python suite, including staging boundaries, ranges, trash, publishing, M1/M2/M3 and Bible tests | **215 passed, 0 failed, 0 skipped**, 58 warnings, 33.36 seconds |
| Existing script suites | **26 suites / 756 checks passed** across scraper, OCR, narration, validator, voice, rendering, review, exports, autopilot, schedule, Drive and settings |
| Renderer parity, included above | 14/14; every-frame minimum SSIM 0.955 versus required 0.95 |
| TypeScript | `tsc --noEmit` passed |
| Next.js production build | Passed; all 12 static pages generated |
| Whitespace validation | `git diff --check` passed |

Initial unit requests failed authentication because the test harness supplied staging authentication to tests designed for in-process unauthenticated fixtures; removing it only from unexposed fixture processes resolved ten failures. Initial mock script calls hit the fixture's zero spend cap; their fake providers were rerun with a local allowance and external networking still denied. Neither involved production authentication or a paid request.

Two existing test dependencies were repaired: Bible assertions predated the documented October 4 cast correction, and the review suite required a developer's on-disk project. Bible tests now use temporary canonical storage and distinguish a character from their title; review uses its own synthetic project. Production canon was not edited.

The broader run also exposed an intermittent merged-project restore conflict. A deterministic high-level regression proved that late QA inspection/error/manifest writes recreated a deleted project. QA now creates only its own `_qa` directory beneath an existing exports directory, tolerates removal during saving and never recreates project/export ancestors. Generated-media regression and the combined trash/range tests pass after the fix. This repair is **not yet on production**.

## Deployment and storage evidence

| Gate | Evidence / current limitation |
|---|---|
| Railway expected commit | `b890f79d-e562-4ada-a7f7-5d383e3dc533`, SUCCESS, `48a1eff`, one running replica, no recent failed deployments |
| Vercel expected commit | `manhwa-studio-8hhia5xti-qwazi12s-projects.vercel.app`, Ready / Production / 33 seconds, filtered by exact `48a1eff`; both Vercel GitHub deployment contexts success |
| Backend health | Verified-TLS `GET /health`: HTTP 200, `{"status":"ok"}` |
| Frontend → backend | Current authenticated Next settings shows backend deploy `48a1eff`; real dashboard/chapter/QA/jobs requests return 200 in backend logs |
| Startup | Persistent volume mapped; original active Chapter 34 restored; application startup complete; four old jobs lacked chapter links and were not resumed. Existing Python escape warnings, no startup exception in the inspected interval |
| Persistent storage | Existing `recap-studio-volume`, 30,000 MB, mounted `/app/data`; live app 20.85/29.44 GB, 62 project folders. Entrypoint/storage mapping and database code unchanged by this release |
| Backups | Settings offers per-project backup downloads. A full-volume backup, verified restore point and restore drill are **not independently verified**; a mounted volume or Drive copies do not prove them |
| Platform notification | Railway reports one unresolved critical notification, zero warnings. Connector does not expose its explanation; browser dashboard requires sign-in. Treat as unresolved rather than dismissing it |
| Pending infrastructure changes | None staged/applying; main-only Railway source; feature-prefix Vercel deployment guards retained |

No new deployment, restart, migration, cache adoption, volume replacement, account reconnect, cap change or enforcement change was performed during this follow-up. Existing legacy projects start and display; that does not prove their missing historical generation identities.

## Live work and smoke tests

**The new mandatory idle gate fails:** authenticated Jobs shows Running 0 and Waiting 4: two interrupted Dark Mage Chapter 105 finalizations, one interrupted Murim Chapter 45 finalization, and one interrupted Murim Chapter 45 publication waiting for YouTube confirmation. They were preserved, not cancelled/resumed to make deployment possible. Posting shows 2 needing review, 19 posted records, 0 ready to post; schedule remains on at 10:00/14:00/22:00 Eastern, maximum 3/day/channel. The passing startup status does not override these gates.

- Authenticated current Next navigation, settings, dashboard/chapter list, Library (15 tracked series) and original Chapter 34 board/Issues load. Existing settings identify the same configured Flamingo Remix target, public default, Charon/energetic style, daily $7 cap, autopilot $6/4 chapters. They were observed without saving or refreshing accounts.
- Original Chapter 34 retains 21 pages, 95 panels/descriptions, 1,591 script words, 78 voice lines and 83 segments; timestamps still October 16. This confirms accessibility/counts, not a full before/after byte audit.
- M1 displays diagnostic mode and no export. M2 displays missing legacy dialogue-transfer history and unverified final critique. M3 shows 80 source lines, 78 artwork descriptions and 85 reference review candidates, explicitly **not semantic coverage**. No repair/redo/accept/approve button was pressed.
- Existing timing UI reports five missing-audio findings for excluded silent slots 66–70 and a 42-second repeated-image hold. These require a project-scoped evaluation; they are not final-MP4 measurements. No automatic correction was applied.
- Playback of an existing delivered MP4 and full-volume backup restoration remain unverified in this pass. The current Watch page can automatically prepare missing SEO/thumbnail assets; it was not used to trigger write-based smoke tests. Existing posted/review records remain visible, which alone does not prove decodable playback.
- Drive is connected and automatically copies video, thumbnail and script after normal render/export, then may remove copied clips. This is existing behavior, not an action taken here. A no-upload test must use a verified no-copy path; do not change Drive/account settings implicitly.

## Chapter 34 and remaining work

The earlier separately approved fresh version `a-regressors-tale-of-cultivation_34-upd-1018` was stopped on the owner's high-priority instruction. Stop is cooperative: the in-flight description batch completed before cancellation. Preserved output: 21 pages, 93 panels, 93 descriptions, estimated $0.41, no script/TTS/timeline/export. Its monitor is paused. The current release brief supersedes continuing that execution; do not resume it automatically.

Original project: https://manhwa.nodepilot.dev/chapter/a-regressors-tale-of-cultivation_34 . It has no exported video, so final-MP4 QA cannot inspect it yet. Future testing should preserve and separately back up this project, inspect available OCR/script/voice/matching without regeneration, resolve silent-slot/long-hold findings, then compare against an explicitly approved distinct fresh run with a verified no-upload render path. Do not treat the partial cancelled version as a completed episode test.

M1 diagnostic QA/exact-export identities, M2 dialogue/review repairs and M3 safe freshness/source preservation are deployed. The deletion safety repair is tested but pending release. Semantic coverage, reviewed legacy adoption and real-media calibration/enforcement remain incomplete; `MEDIA_QA_ENFORCE` remains off as confirmed by diagnostic UI. External Investigator/shared Series/Episode architecture and story-grounded audience research remain plans; broad collectors, paid integrations and account changes are not implemented/enabled.

Next independent safe task: review the narrow safety PR and collect read-only evidence for the four waiting records, the critical platform notification and a verified backup/restore point. Recheck all gates before any main merge. If a deployed fault requires recovery, preserve diagnostics and volume data; use an idle, verified rollback to the prior application version without resetting storage or weakening safeguards.

Reviewable safety repair: https://github.com/qwazi12/manhwa/pull/8 . Evidence-only dependency: https://github.com/qwazi12/manhwa/pull/7 . Both remain draft/unmerged.
