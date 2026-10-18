# Flamingo production verification — October 18, 2026

The completed M1–M3 implementation was already released under the earlier owner instruction. The owner now authorizes merging/deploying PR #8 **if all gates pass**. **Those gates have not passed; no additional main merge or deployment occurred.** A newly reproduced QA/deletion race is fixed and tested on `dev/flamingo-release-safety`, pending safe deployment conditions.

## Latest persistence checkpoint (October 18, afternoon Eastern)

Authenticated Railway Console confirms that `review_ui/data` is a real container directory outside the volume. The current database has **72 project rows**, unlike the empty pre-migration snapshot. The verified helper prepared `/app/data/studio-ops` without changing the running source path. It copied the populated SQLite database and both backup files; all nine files for the four protected historical jobs and stopped Chapter 34 retain their original hashes.

The copied database was downloaded through Railway Files and independently restored locally: **139,264 bytes**, SHA-256 `314cf3dda3a9f5436c2b650e3362dee4f29b061301aa56966a5060824111d626`, matching the live migration manifest; integrity/FK checks pass and the restored SQL dump is identical, with 72 project rows. No production restore was attempted. A fresh **21.08 GB volume backup** is available at **October 18, 14:38 Eastern**; the October 5 backup remains. There is no automatic volume-backup schedule; none was changed.

The alert is now identified: an **80% volume-capacity notification from five days earlier**. Current volume capacity subsequently changed from 30,000 to **50,000 MB**, with the same deployment/replica still online. Alerts remain enabled at 80/95/100. The connector still reports the old critical notification; do not claim its platform resolution state has cleared merely because present usage is below threshold. No alert was dismissed or monitoring disabled.

PR #8 now also contains the persistence entrypoint/helper and an ingest-recovery scope repair. Startup previously treated typed render/publish history as interrupted ingests and rewrote shadow records; its regression fails before the filter fix. Recovery now excludes those records, while genuine interrupted/paused ingests retain their existing resume behavior. The live copy is **prepared only**, not activated. New startup links the verified directory before uvicorn; it retains originals and refuses missing preparation, conflicts, drift or copy/validation failures. See [the runbook](FLAMINGO_PERSISTENCE_RUNBOOK.md). Latest combined validation: **236 passed, 0 failed, 0 skipped, 58 warnings**, 36.35 seconds. This includes 15 persistence regressions and six startup-scope cases; six relevant existing script suites pass **158/158** checks. The prior 26-suite/756-check renderer/pipeline baseline remains recorded below. Fresh TypeScript and Next production build revalidation also passes for this combined candidate. The initial new recovery fixture omitted `RESUME_MAX` and failed two legitimate-ingest cases; adding that fixture constant resolved both. Merge/deployment remains conditional on the final gates.

Final live preflight: `/api/jobsbar?failed=1` returns `jobs: []`; logs show no running work and the same four historical Waiting IDs. Raw posting queue has **20 posted / 32 removed / zero queued or posting**; render queue has **39 done / one error / zero waiting or rendering**. Existing schedule remains 10:00/14:00/22:00 Eastern, max three/channel/day. Chapter 34 is cancelled at $0.407. The filesystem now reports **49.13 GB total / 28.26 GB free**. All nine protected job files retain their pre-copy hashes. A second fresh restore point labelled **Online resize to 50000MB**, 21.08 GB, is now also visible alongside the manual 14:38 backup.

**Owner decision pending:** the exact candidate is prepared/tested, but Railway still reports the old capacity event as critical and the four completed historical records remain labelled Waiting. The owner was asked whether to keep holding the strict status gates or explicitly accept these documented historical statuses while leaving records and monitoring unchanged. No exception is inferred from silence. Until that decision or actual platform clearance, do not merge or activate the new startup mapping.

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
| #8 | QA deletion race, persistent ops backups, scoped startup recovery and test fixtures | Draft, `dev/flamingo-release-safety`, stacked on #7; tested code commit `b48b721`; not deployed |

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
| Persistent storage | Same `recap-studio-volume`, now 50,000 MB at `/app/data`; existing project mappings retained. Populated ops DB/backups copied and verified on volume, startup link activation still pending |
| Backups | Current populated SQLite copy passes checksum/integrity/isolated restore with 72 projects; fresh 21.08 GB Railway volume restore point created October 18 14:38 Eastern. Original Chapter 34 archive restores 125 files/78 valid voice MP3s. Full-volume restore drill not performed |
| Platform notification | Authenticated dashboard identifies the old 80% volume alert; present capacity 50 GB and usage well below threshold. Connector still reports one critical/zero warnings; platform resolution state remains uncleared |
| Pending infrastructure changes | None staged/applying; main-only Railway source; feature-prefix Vercel deployment guards retained |

No new deployment, restart, cache adoption, volume replacement, account reconnect, spending-cap change or enforcement change was performed. The later owner-authorized preparation added a verified ops-data copy on the existing volume and a fresh volume backup; it did not switch the running source. Existing legacy projects start and display; that does not prove their missing historical generation identities.

## Live work and smoke tests

**The mandatory idle gate remains uncleared:** authenticated Jobs shows Running 0 and Waiting 4. Read-only review establishes later successful outcomes for these historical records, but their stored interrupted status remains unchanged. They were preserved, not cancelled/resumed to make deployment possible. Posting shows 2 needing review, 19 posted records, 0 ready to post; schedule remains on at 10:00/14:00/22:00 Eastern, maximum 3/day/channel. The passing startup status does not override these gates.

| Waiting record | Stored state | Read-only evidence / disposition |
|---|---|---|
| `684b271aa3b2` — Dark Mage 105 finalize | Interrupted, export 69/69 | Later successful finalize `fc6c2fb4d8a5` produced `final_Oct11_03.17PM_1.1x.mp4`; chapter now merged. Leave historical record untouched |
| `97b762094d26` — Dark Mage 105 finalize | Interrupted, render 14/85 | Same later successful export; chapter has no render queue or active render job. No rerender |
| `e7deae78a155` — Murim 45 finalize | Interrupted, render 56/75 | Later export was approved and published; chapter now archived. No rerender |
| `ea244d5ffbb8` — Murim 45 publish | Interrupted, confirmation 3/4 | Downloaded project archive's `publishes.json` identifies this exact job as published/done with a platform post ID and later completion timestamp. Do not retry an already confirmed publication |

These findings explain the mismatch between job history and completed project/publication records. They do not clear or mutate that history. The publish-status refresh endpoint was deliberately avoided because it can update stored publication records; no upload or remote publication operation was requested.

## Backup verification follow-up

The authenticated current-site `/api/backup/status` reports an enabled SQLite backup, one fresh local snapshot and no configured Drive backup destination. The existing snapshot `manhwa_20261018_133829_pre_migration.db.gz`, created **2026-10-18 17:38:29 UTC**, was downloaded without creating or restoring anything on production. Its 122,880 decompressed bytes match the recorded SHA-256 `753d5876abe86c721c9c1557ef0a1dee7d477a19edcbe65a1a187b30ce7c8185`; gzip CRC, SQLite integrity, foreign-key checks and an isolated local SQLite restore all pass. **All six tables have zero rows.** This is a valid database snapshot, not a backup of the existing file-based projects, queues, settings, voice or media.

The live snapshot path is `/app/manhwa-recap-v1/review_ui/data/backups/…`. `ops_config.py` selects explicit `MANHWA_DATA_DIR`, otherwise writable `/data`, otherwise `review_ui/data`; Railway mounts the volume at **`/app/data`**, and its configured variable names contain neither `MANHWA_DATA_DIR` nor `DATABASE_URL`. The entrypoint persists projects, segments-workspace and review_crops, but does not map `review_ui/data`. **The live Console realpath check now confirms container-local database/backups.** The populated current database/backups have subsequently been preserved and independently verified as described in the latest checkpoint. Running-path activation is deferred to the reviewed entrypoint at a gated release; never switch a live database path or discard either copy to force migration.

An on-demand, read-only archive of the **original** Chapter 34 project was also downloaded and restored into a private local directory. Archive SHA-256: `40782ae73b18b58699b79eb35cf51d8eda8d9fda4c1a0bcb02dd2c7f5827f33c`; 25,527,473 bytes. All **125 restored files** match their archived hashes: 21 pages, 95 descriptions, 83 segments, 1,591 script words and 78 voice MP3s. Every voice file passes ffprobe; total voice duration 527.72 seconds. The existing archive endpoint excludes `crops/` and `clips/`, so this is **partial project recovery**, not a complete artwork/full-volume backup. Murim 45's separate archive was downloaded only to inspect existing review/publication metadata. Archives, source art and private manifests remain outside Git.

Railway plugin access is working. Its exposed tools supply health, deployment, storage configuration, metrics and logs, but no alert-detail/acknowledgment or volume-backup-history operation. The owner completed normal browser sign-in, and the dashboard Console/Files now provide the missing runtime evidence without a CLI reconnect. Latest status remains **Online, 1/1 replica, zero recent deployment failures, no pending changes and one critical notification**. Its known old capacity event must be distinguished from present usage and from the platform's uncleared resolution flag; no notification was dismissed to manufacture a passing gate.

## Existing smoke-test details

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

Next independent safe task: prepare an offline, regression-tested persistence/backup migration plan and a read-only job-history reconciliation preview, preserving every job and artifact. Runtime alert details and full-volume backup history still need an access path that exposes those operations. PR #8 is mergeable with a successful CodeRabbit status, but these operational gates prevent release. Recheck all gates before any main merge, account for the evidence-only #7 dependency, and avoid redundant production restarts.

Reviewable safety repair: https://github.com/qwazi12/manhwa/pull/8 . Evidence-only dependency: https://github.com/qwazi12/manhwa/pull/7 . Both remain draft/unmerged.
