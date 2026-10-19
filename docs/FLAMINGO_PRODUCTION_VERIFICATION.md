# Flamingo production verification — October 18, 2026

**PR #8 merged and deployed successfully at `88920f32dc965f3c38d7c3c398bbd7d92c6a0ac3`.** The current Next.js studio is https://manhwa.nodepilot.dev/ . Railway is Online 1/1 with zero critical notifications, zero warnings, zero recent deployment failures and no pending work. Database/backup persistence is active and independently checked after restart. All protected files match their pre-release hashes. Chapter 34 remains stopped; the four historical Waiting records are untouched under the owner's explicit historical-status exception.

## Authorization and release inventory

The owner authorized resolving the alert, safely repairing backup persistence and merging/deploying #8 when gates pass, then explicitly selected **Approve historical-status exception**. That permits the four verified completed historical jobs to retain their inconsistent Waiting labels without cancelling, resuming or rewriting them. The old capacity notification actually cleared before the merge, so it required no exception. All other worker/data/release checks passed. No account, publishing destination, alert threshold or paid-processing authorization was expanded.

| PR | Disposition |
|---|---|
| #1–#4 | M1/M2/safe M3 and hardening; already merged in the earlier integrated release |
| #5 | Research/shared-intelligence architecture documentation; no Investigator implemented |
| #6 | Earlier integration merge `48a1eff1cacad241c35f87b07721a04adad11a79` |
| #7 | Evidence commits included through #8 ancestry; closed/merged, no separate main update or deployment |
| #8 | QA deletion race, ops persistence, scoped startup recovery, isolated fixtures and evidence; merged once at `88920f3` |

The exact approved #8 head was `9c8183522055d03b7b1b67643dc95a546a78aa34`; tested application code was `b8ecafc0cf775316a97ff8a8f6a4a22a8d46aa33`, followed only by documentation checkpoints. The stacked branch was retargeted to main and marked ready before the normal expected-head merge. No force push, manual deployment or redundant merge occurred. Previous main `48a1eff` is the rollback reference, subject to the persistent-storage precautions in the runbook.

## Deployed runtime verification

| Gate | Verified result |
|---|---|
| Railway | Deployment `4539f8a5-ec0d-4c93-8f4c-4f8efa6669e4`, exact `88920f3`, automatic build from 18:58:23 UTC; SUCCESS, one running replica |
| Vercel current studio | `dpl_FXHZBQrzcBbGbGQp9VaQczv17vuQ`, `manhwa-studio-dgy7nuc5z-qwazi12s-projects.vercel.app`, exact `88920f3`, Ready/Production; alias `manhwa.nodepilot.dev` points to it |
| GitHub deployment contexts | Railway and both Vercel project contexts report success for exact merge SHA |
| Startup | Existing volume mapped; ops helper reports linked target `/app/data/studio-ops`; application startup complete at 19:00:19 UTC |
| Health | Verified-TLS `/health` returns HTTP 200, `{"status":"ok"}`; one earlier request returned 502 during startup transition |
| Frontend → backend | Current authenticated Next Settings visibly reports `live · deploy 88920f3`; original Chapter 34 board/Issues and Library load |
| Storage | Same 50,000 MB volume at `/app/data`; `review_ui/data` is now a symlink resolving to `/app/data/studio-ops` |
| Populated database | Read-only SQLite integrity `ok`, zero foreign-key errors, 72 project rows retained |
| New startup backup | `manhwa_20261018_150014_pre_migration.db.gz` resolves under `/app/data/studio-ops/backups`; gzip decode and isolated temporary SQLite restore pass, 72 projects, zero FK errors |
| Preserved artifacts | 9/9 protected job files, 153/153 original/fresh Chapter 34 files and 3/3 queue/settings files retain exact pre-release SHA-256 hashes |
| Live work | Jobs bar empty; running jobs empty; same four historical Waiting IDs; fresh Chapter 34 job `c5b1067541f1` remains cancelled |
| Platform | Zero critical/zero warnings, no recent failed deployment, no staged/applying changes; alert monitoring remains enabled at 80/95/100 |

The new backup's raw SHA-256 is `314cf3dda3a9f5436c2b650e3362dee4f29b061301aa56966a5060824111d626`, identical to the populated pre-release snapshot. Backup status reports enabled, fresh/no warning, two local copies and its existing next scheduled database run. Drive upload for database snapshots is not configured. This is distinct from the existing connected video-copy Drive.

Inspected deployment logs contain normal startup and existing Python invalid-escape SyntaxWarnings, with no startup traceback or job-resume event in the inspected interval. Browser warning/error collection was empty during the original chapter/settings/Library smoke. A bounded 35-second Vercel runtime-log subscription during read-only smoke produced no new runtime entries; it does not cover historical requests. These are bounded checks, not a guarantee about every future request. Feature checkpoint pushes remain guarded from production deployment.

## Backups and persistence repair

The old ops path was a real container-local `review_ui/data` directory, outside Railway's `/app/data` volume. The early 17:38 UTC pre-migration backup had six empty tables and did not represent the subsequently populated 72-project database. It was not restored over production.

A pinned, SHA-verified helper prepared three files on the existing volume without switching the running source: current SQLite database, backup status and earlier compressed backup. It uses SQLite online backup to fold committed WAL, validates integrity/FK and checks ordinary-file fingerprints, preserving originals and refusing conflicts/source drift. Default CLI behavior is copy-only; `--activate` is reserved for entrypoint startup. The authorized release activated the verified target before uvicorn. See [the persistence runbook](FLAMINGO_PERSISTENCE_RUNBOOK.md).

The populated pre-release database was also downloaded and restored locally: 139,264 bytes, raw SHA above, identical SQL dump and 72 projects. Post-release startup produced a new populated compressed snapshot on the persistent volume, verified independently in a temporary restore directory. No production database restore occurred.

Railway dashboard confirms a **21.08 GB manual full-volume restore point at October 18, 14:38 Eastern**, plus an **Online resize to 50000MB** restore point of the same size. The October 5, 3.72 GB backup remains. **No automatic volume-backup schedule is configured and no full-volume restore drill was performed.** Existing database snapshot scheduling is separate. No schedule was changed during this release.

The volume changed from 30,000 to 50,000 MB while the owner was actively using Railway. The agent inspected the resize dialog but did not enter confirmation text or submit Resize. Same volume/mount and service retained. Latest pre-release filesystem reading was 49.13 GB total / 28.26 GB free. The old five-day 80% notification later cleared in the connector; no dismissal or threshold reduction was used.

Original Chapter 34's private archive also passes isolated restore: 125 files, 21 pages, 95 descriptions, 83 segments, 1,591 words, 78 valid voice MP3s totaling 527.72 seconds; all file hashes match. Archive SHA-256 `40782ae73b18b58699b79eb35cf51d8eda8d9fda4c1a0bcb02dd2c7f5827f33c`, 25,527,473 bytes. The archive endpoint excludes crops/clips and is partial project recovery. Source art, archives, database copies and private manifests remain outside Git.

## Four historical Waiting jobs

| Record | Existing state and verified later outcome |
|---|---|
| `684b271aa3b2` — Dark Mage 105 finalize | Interrupted export 69/69; later finalize `fc6c2fb4d8a5` produced `final_Oct11_03.17PM_1.1x.mp4`; chapter merged |
| `97b762094d26` — Dark Mage 105 finalize | Interrupted render 14/85; same later successful export; no active render queue |
| `e7deae78a155` — Murim 45 finalize | Interrupted render 56/75; later export approved/published; chapter archived |
| `ea244d5ffbb8` — Murim 45 publish | Interrupted confirmation 3/4; persisted publication history links this exact job to published/done and a platform ID; do not retry |

All four retain their existing labels and durable records. Each has both a typed `render_ID.json` and a plain shadow record; these eight plus Chapter 34's cancelled record account for nine protected files. The old startup path accidentally treated typed render/publish history as ingests and rewrote shadow errors. PR8 filters that scope while preserving genuine ingest recovery. Deterministic regressions failed before the fix; all nine live hashes now also survive the actual deployment.

Posting raw queue remains 20 posted / 32 removed / zero queued or posting; render raw queue 39 done / one error / zero waiting or rendering. Existing schedule remains enabled at 10:00/14:00/22:00 Eastern, max three/channel/day. These queue/settings files match pre-release hashes. No historical job was cancelled, resumed, reconciled or published.

## Offline validation and repairs

| Check | Actual result |
|---|---|
| Final combined Python suite | **236 passed, 0 failed, 0 skipped**, 58 existing warnings, 36.35 seconds |
| Included persistence / startup scope cases | 15 / 6 passing regression cases |
| Six relevant existing scripts | **158/158**: job control 41, sweep 16, limits/resume 6, autopilot 55, render queue 15, posting schedule 25 |
| Earlier unchanged pipeline baseline | 26 suites / 756 checks, including renderer 14/14 and minimum SSIM 0.955 |
| Fresh frontend verification | TypeScript noEmit and Next production build pass, 12 static pages |
| Syntax / patch checks | Shell syntax, Python compilation and `git diff --check` pass |

Tests use an isolated snapshot, temporary mutable data, fake providers and denied Python external networking. No production credentials, paid calls or account operations. Earlier harness authentication failures, zero fixture budget and stale Bible/review fixture assumptions were corrected only in the isolated tests. Production canon and caps were unchanged. The first new recovery fixture omitted `RESUME_MAX`; its two NameErrors were fixed before the all-pass run. Initial WAL fixtures exposed transient SHM coordination and copied journal-mode behavior; the helper now ignores only transient SHM and creates self-contained SQLite snapshots.

A late QA write could recreate deleted project/export ancestors and block Restore. `_save` now creates only its own QA directory beneath an existing exports directory and tolerates its removal; generated-media regression fails before and passes after the fix. This repair is now deployed through #8.

## Current studio smoke and remaining boundaries

- Original Chapter 34 board and Issues load with unchanged October 16 artifacts: 21 pages, 95 panels/descriptions, 1,591 words, 78 voice lines, 83 segments, no export. Diagnostic mode remains off for enforcement; no repair/render/approve/publish buttons were used.
- Issues truthfully shows missing legacy dialogue-transfer history, unverified final critique, 80 source lines / 78 artwork descriptions / 85 reference candidates and semantic coverage unassessed. Five missing-audio findings refer to excluded silent slots 66–70; a repeated-image hold totals about 42 seconds. These need scoped evaluation, not automatic changes.
- Settings shows the same existing narrator, channel/privacy, spending caps, schedule and connected video Drive. Normal export automatically copies to Drive and can remove copied clips. A future no-upload render test requires a verified no-copy path.
- The cancelled partial `upd-1018` variant displays **unknown chapter** after restart. Its directory, 21/93/93 source outputs and cancelled durable job survive unchanged, but it has no `project.json`. Existing chapter listing relies on in-memory ingests for such partial folders; startup does not reload cancelled records. AST comparisons confirm `_resume_interrupted`, `_ingest_by_project`, `_chapter_rows` and `chapter_view` are unchanged from `48a1eff`. Record this restart visibility defect for an offline read-only repair; do not resume or fabricate completion metadata to hide it.
- Playback of a delivered final MP4 and full-volume restore are not verified here. Real-media calibration, semantic coverage and reviewed legacy adoption remain incomplete; `MEDIA_QA_ENFORCE=0`.

## Review readiness and next safe work

**Ready for review:** M1 diagnostic/exact-export implementation, M2 dialogue/provenance and critique lifecycle, M3.1–M3.4 cache/source preservation/hardening, plus deployed PR8 safety/persistence/recovery fixes. **Still blocked:** M3 semantic coverage and legacy adoption, final-media calibration/enforcement and a completed fresh Chapter 34 end-to-end run. Chapter 34 stays stopped at the owner's instruction, estimated cost $0.407, no new script/TTS/timeline/export or publication; its monitor remains paused.

Research/shared intelligence remains the documented architecture: extend existing Bible/identity and source/provenance contracts into shared Series/Episode projections, keep external dated observations separate from story canon, ground every SEO/title/thumbnail promise in episode events and audience evidence, and trace QA through exact exported/published identities. No broad external integration or paid research is enabled.

Next independent safe task: reproduce cancelled partial-chapter visibility across restart with offline fixtures, then design a read-only durable-history projection that leaves cancellation and job files unchanged. Continue with Unicode-safe Bible identity lookup and shared Series/Episode projection fixtures before dependent live research. No additional main merge or deployment is authorized by this evidence checkpoint.

## Follow-up: cancelled partial-chapter visibility (review-only)

The unknown-chapter defect is repaired on dev/flamingo-cancelled-chapter-visibility, not deployed. `_ingest_by_project` reads durable jobs for existing folders and prefers live records without repopulating worker memory or enqueuing recovery. Render/typed records stay excluded; missing folders stay absent. Chapter detail includes cancelled status and the UI says Stopped by you without a new resume button. Regression initially failed with the real404; all9newcases plus6startup-scope cases pass, integrated245pytest/189script checks/TypeScript/Next build pass. Fresh live hashes remain9jobs+153Chapter34+3state unchanged; same4Waiting records and Chapter34cancelled, production88920f3. [Overall status](FLAMINGO_CURRENT_STATUS.md) separates implemented capabilities, repairs and unbuilt architecture.


## Later PR9 release and isolated test checkpoint

PR9 merged once at91bfd89515b41aae445ceadda7d8f57bc68282f3 after245Python tests and prior189script/tsc/build results, expected-head/CodeRabbit checks, a new18:08Eastern17.54GBfullvolume restore point, populated72projectSQLite/current compressed-backup restore integrity/FK checks, idle raw queues and protected-file checks. Railway f13c028b-57b2-4c5e-a976-7c7f82e8105a SUCCESS and Vercel dpl_5dyc8Tz2yZWGnDWFKGM8zG3jjMVX Ready/Production both match91bfd89; canonical alias retained. Cancelled chapter now loads with Stopped by you. Post-test recheck retained9job/153chapter/3state hashes exactly,0running and the same4historical Waiting. PR10 isolated studio remains separate/unmerged; real MP4 and failed media-QA evidence are in [the E2E report](FLAMINGO_CH34_E2E_REPORT.md). No production chapter regeneration, publication, scheduling or account/destination change.


## October 18 — approved PR 10 rollout and live QA

Production is now 5fec5c7 (PR 10), automatically deployed after 262 Python tests, 419 script checks, 28 isolation regressions, TypeScript/Next build and fresh idle/backup/preservation gates. Railway a07baa62-2277-4f0f-a09d-b88b94a5e02c SUCCESS/Online 1/1, health 200; Vercel dpl_2uysdSFBRdPrqC4i9HDco3f9WBwd Ready/Production with canonical alias. The old critical notification is historical; the operational health connector reports no current critical alerts. Retained owner exception applies to historical statuses only.

New QA-only project _34-liveqa-1018 completed one live ingest and one render. MP4 228,109,843 bytes, 1920×1080/30 fps H.264/AAC, 703.700 seconds video / 703.690 seconds audio, zero stream start times; full decode and fresh final-media QA pass, with one pause warning. No semantic speech pass or calibration is claimed. Current Next playback advances without media error. Corrected only the new export's title/description/tags/badge; private, targets empty, no publish time, no Drive record.

Normal render-history retention pruned one oldest completed I Am the Fated Villain Chapter 361 row. Audit caught it; with no active workers, preserved the QA completion and queue snapshot in this new project's owner_review directory and restored the original queue bytes. No retry/cancel occurred. The original/cancelled Chapter 34 and nine protected job files were unchanged throughout; final hashes are checked separately after restoration. See the live report for this incident and the remaining misleading excluded-slot diagnostics.

Before rollout: live database and compressed startup backup integrity ok/FK 0, isolated temporary restore 72 projects, raw SHA 314cf3dda3a9f5436c2b650e3362dee4f29b061301aa56966a5060824111d626. Full-volume 18:08 Eastern / 17.54 GB restore point available. No full-volume restore drill or automatic full-volume schedule was performed.
