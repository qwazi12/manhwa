# Chapter 34 end-to-end test — isolation gate

October18,2026. **Stopped before merge/deployment or generation under the owner's explicit isolation stop condition. No new test project, chapter processing, paid request or MP4 was created.** PR9 remains open/unmerged; production remains88920f3. This is a blocker assessment, not a completed episode-test report.

## Current authorization

The owner conditionally authorizes PR9merge/deployment after tests, backups, idle and preservation gates, followed by an isolated full Chapter34test. Preserve the original and cancelled variant and all four historical Waiting records. Disable publishing/scheduling/automatic Drive and other external side effects for the test. If isolation cannot be guaranteed, stop and explain. This request authorizes the intended test; the blocker is missing verified isolation, not a request for repeated permission to generate.

## Concrete blocker found in the current code

| Path | Actual behavior | Consequence |
|---|---|---|
| `review_ui/server.py::_after_export` | Starts media QA, `_prepare_publish`, QC warming and `_drive_copy` when Drive is configured | A separate project ID inside the production studio does not disable automatic external uploads |
| `_drive_copy` | Copies video/thumbnail/script to connected Drive and can free render clips after a successful copy | Do not render in the production process merely assuming an isolated project name is sufficient |
| `_scheduler_pass` | Runs budget recovery, archive, posting, demand, source checks, packaging, autopilot and backup work | The test must have a separate stopped scheduler and separate state; changing production-wide settings would affect existing studio behavior |
| `deploy/staging/prepare.py::environment` | Credential allowlist, separate HOME/database/state, scheduler off, zero budget, offline mode | Existing staging was deliberately built for offline checks; it is not a configured real-generation test environment |
| `deploy/staging/staging_backend.py::create_app` | Rejects Gemini/TTS and external-service credentials | Real description/script/narration cannot run in this profile |
| `deploy/staging/sitecustomize.py` | Blocks non-loopback Python network connections | Source downloads and real AI/TTS calls cannot run; this is an application guard, not an OS network sandbox |

There is **no tested provider-only generation profile** connecting these controls: permit required source/Gemini/TTS traffic while fail-closed blocking publication, social, Drive, account changes and automatic scheduling in both HTTP and background/subprocess paths. Removing the offline guard or adding credentials ad hoc would not establish the requested guarantee. No such bypass was attempted.

The original Chapter34, cancelled variant and all historical jobs were not mounted/copied into a new runner or modified. No production job-control, schedule, account, settings or publishing request was made during this assessment.

## Release checks and actual disposition

- Existing PR9candidate code previously passed245Python tests,189existing script checks, TypeScript and Next production build. No application code changed in this assessment; tests were not redundantly rerun after the isolation stop.
- Fresh GitHub read: PR9open,unmerged,mergeable; exacthead59060b84bae50174a732023b904f5410238a077b before this documentation checkpoint; CodeRabbitsuccess. Main88920f32dc965f3c38d7c3c398bbd7d92c6a0ac3.
- Fresh Railway read: existing deployment4539f8a5-ec0d-4c93-8f4c-4f8efa6669e4 SUCCESS/Online1of1,zero critical/zero warnings/zero recent failures/no pending work.
- Current backup restoration, raw production queues and protected-file hashes were **not freshly revalidated for a release after the stop**. Their previous passing evidence remains in the production report; platform Online alone is not an idle/data-preservation check.
- Because the isolation prerequisite failed, no claim is made that all release gates now pass. No merge/deployment/restart was performed. The previous explicit exception for four historical Waiting labels does not authorize altering those records.

## What must be verified before the test can run

Build a separate, reviewable real-generation sandbox on the same updated studio code, leaving the existing offline profile intact:

1. Separate source checkout, project/media/database/usage/cache/HOME, fresh authentication and no production volume mount or shared mutable project directories. Keep the original/cancelled project and four historical jobs outside the runner.
2. Copy only the authorized provider credentials required for this test through a secret-safe mechanism; exclude all publishing, social, Drive and account credentials. No connected-account or production destination changes.
3. Enforce permitted source/AI/TTS network access and denial of upload/publish/account destinations across backend, background workers and child processes. Test forbidden calls with sentinel/fake transports before allowing real calls; missing controls/configuration must fail closed.
4. Disable automatic scheduler/autopilot, recovery, posting and Drive copy in the test process, with regression tests at actual export and worker boundaries. Do not change global production settings. Retain normal pipeline logic and label any test-only suppression clearly in evidence.
5. Present an authenticated isolated Next studio to the owner with a clear test identity and only its own backend/media. Do not repoint the production frontend. Verify it cannot access production jobs/projects or send forbidden operations.
6. Preflight the exact release/test commit, current populated backup/restore, current real worker/processing queues, original/cancelled/four-job baselines, storage and a measured cost limit before merge/test launch. Existing successful checks must not be silently carried forward as fresh release evidence.
7. Capture each real stage: ingestion/source hashes, OCR, descriptions, script/provenance, narration/audio, matching/timeline, clips/finalMP4, decoded QA, SEO and thumbnail artifacts. Report failures/incomplete checks explicitly. Confirm playable video in the isolated updated studio; preserve hashes before/after and prove no upload/scheduled publication occurred.

No source/generation/render/SEO/thumbnail stage of this new requested run has executed. The existing partial cancelled run is not substituted for this test. A playable finalMP4 and a completed stage-by-stage quality report cannot be delivered until the isolation gate is implemented and verified.
