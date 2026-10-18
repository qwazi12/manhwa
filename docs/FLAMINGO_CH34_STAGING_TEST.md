# Chapter 34 integrated staging test — preparation checkpoint, October 18, 2026

## Actual status

**Local staging launched after owner approval; no chapter processed.**
The owner typed **“launch”** on October 18 after reviewing the $0 local proposal.
The running, authenticated studio is **http://localhost:3138/** on this Mac;
FastAPI listens only at **http://127.0.0.1:8138**. Both run candidate `9abeb62`.
The browser is signed in and left open. No cloud resources were provisioned.

Live checks passed: signed form login and session cookie, authenticated dashboard,
frontend-to-backend project/home/studio requests (200), unauthenticated API
rejection (401), and real publishing/OAuth rejection (403). Browser error/warning
logs were empty at handoff. No source project metadata exists in the snapshot;
the projects API retains the legacy empty-workspace fallback “chapter-2 (current)”
with zero segments, which is not an imported chapter. The actual dashboard shows
zero boards/videos, posting off and a $0 cap. Use **localhost** consistently:
Next canonicalized the initial numeric-loopback login redirect to localhost, so
mixing those hostnames loses the host-scoped cookie. The localhost login works.

Paid model calls remain unapproved/disabled. The original production Chapter 34
has not been modified or copied. No Chapter 34 project, source processing or final
MP4 is available in staging yet. Queue simulation remains unimplemented.

Branch `dev/flamingo-staging-ch34` starts from integrated `fc32713268c312bf248748753bff9468cf239bfa`.
The prepared candidate is **`9abeb627ae88d4dd5a817098dae1dc351a77dc59`**
(both frontend and backend); subsequent documentation-only checkpoints do not
change that snapshot. Its Next production build passed. An in-process test of
the actual FastAPI startup verified shared-secret authentication, project reads,
blocked publishing/OAuth and a stopped scheduler; no TCP listener was launched.
The integrated app is the existing studio, not a replacement or mock UI.
PR #5 contains architecture documentation; it does not implement an Investigator,
shared Series/Episode projections or the planned connected SEO architecture.

The GitHub API confirmed PRs #1–#5 open, draft and unmerged, with dependencies
#1 → main, #2 → #1 branch, #3 → #2 branch, #4 → #3 branch, #5 → #4 branch.
Ancestry checks confirm the M1, M2, M3 and hardening implementation checkpoints
are included in `fc32713`. Railway production still follows `main`; both Vercel
configs disable Git deployment for `dev/flamingo-*`. No main push or deployment.

## Working, repaired and still to build

| Category | Evidence and limit |
|---|---|
| Existing implementation ready for review | M1 diagnostics/export identity; M2 ordered OCR/provenance and truthful critique; M3.1–M3.4 cache fingerprints, manifests, source preservation and deterministic linkage. Integrated isolated regression run: **104 passed**, 7 existing warnings, 15.30 seconds. Test-created media/fakes are regression fixtures, not this chapter. |
| Preparation repair | A frontend dependency symlink crossed repository roots and caused a React Client Manifest failure during prerendering. Copying dependencies and cleaning the snapshot build output fixed it; isolated Next production build passed. No product code workaround for that build failure. |
| New staging preparation | Committed-source snapshot tool, separate SQLite/data/HOME/media, fresh private auth, credential allowlist, offline Python network guard, disabled account/publish/automatic-job routes, explicit launch approval flag and fail-closed staging frontend proxy. **20 new boundary tests pass**, including actual Next middleware execution. No service launch. |
| Still to build for this test | Explicitly labeled queue/scheduling simulation; approved provider-only paid profile; complete stage evidence/report capture and real-media targeted repair exercise. The launcher's default profile cannot generate paid artifacts. |
| Still blocked in the product roadmap | M3.5 semantic coverage, M3.6 reviewed legacy adoption and real-media QA calibration. No historical inputs guessed; all-reference coverage never proves story coverage. |

The installed renderer also passed its existing **14/14** generated-fixture
regressions: four FFmpeg clips rendered in 4.2 seconds; exact streams/durations,
audio placement/silence, visual modes and overflow refusal passed. Frame-level
parity with the installed HyperFrames renderer passed (minimum SSIM **0.955**,
threshold 0.95). This verifies local renderer operation, not chapter quality.
HyperFrames doctor found Node24, ffmpeg/ffprobe8.1.2 and system Chrome. Its overall
`ok=false` reflects absent optional Whisper/Kokoro/MusicGen and a stopped Docker
daemon; the successful native render does not need Docker. No model was installed
or called. About **9.9 GB** free disk remained after removing only the redundant
scratch source snapshot; original data and retained evidence were not removed.

## Approved local launch resources (paid processing still requires approval)

Recommended first environment: the owner's existing Mac, with one backend and
one frontend process, no additional cloud resources/subscriptions. Additional
infrastructure bill: **$0**; local CPU, electricity and disk are used. Stop when
review finishes. Snapshot source/dependencies need roughly 1 GB before chapter
media; allow several additional GB for clips/MP4s, to be measured after source
identification. No hosted server, volume, public DNS or tunnel will be created.

| Resource | Proposed boundary |
|---|---|
| Next frontend | `http://127.0.0.1:3138`, existing login, unique local credentials, staging-only backend rewrite |
| FastAPI backend | `http://127.0.0.1:8138`, new shared secret, actual updated app wrapped by offline controls |
| State | New private directory below repo `work/`; source/project/media tree separate from canonical checkout; isolated SQLite database and HOME |
| External access | No provider/account credentials inherited; Python external connections denied; publishing/account/autopilot routes disabled |
| Availability | This Mac only, while both processes run. Not yet deployed or owner-browser verified. A remotely accessible cloud preview needs a different approval proposal. |

Local launch was approved and executed; that approval is independent of spending approval. Approving this proposal
alone does **not** authorize model calls, source-network downloads or publishing.
Source identification can proceed read-only once an authenticated studio view
or a verified chapter URL/source-only export is available. Never request API keys
or production passwords in chat; the owner can sign into the existing studio.

For a cloud alternative, obtain approval for a **new** Railway service/environment
and volume plus a separate staging frontend with enforced access protection,
after verifying the actual account plan/credit requirements. Do not clone live
variables, mount the production volume or repoint the production frontend.
No cloud price or preview protection is assumed from the existing account.

## Source and paid-processing prerequisites

Target: **A Regressor's Tale of Cultivation, chapter 34**, fresh project identity
`staging-regressors-tale-ch34-<run-id>` (proposed; none created yet). No matching
local project was found. The original production project and correct source URL
are **not verified**: the browser reached the login form, the connector cannot
read sealed application secrets, and the installed Railway CLI lacks SSH.
No production secret was changed/recreated and no source identity was guessed.

Once authenticated read-only access is available: record the original project
ID, chapter/series metadata and exact source URL; verify artwork/chapter header
and chronology. Copy **source pages only** into the new tree if authorized and
appropriate. Record file hashes and source-copy lineage; do not import description,
script, Bible research, voice, manifest, approvals, queue or autopilot artifacts.
Record original source hashes before/after when safely available. A fresh staging
Bible may be derived through the app's current path, with its evidence limits.

No paid call has been made. Locally available Gemini credentials were detected
by presence only; none enter the offline snapshot. Tesseract is missing locally,
so offline fallback OCR is unavailable. ffmpeg/ffprobe, Python FastAPI/Uvicorn,
OpenCV/Pillow/SQLAlchemy/Google SDK, Node and Chrome executables are present;
renderer discovery and generated-fixture rendering are now verified as described
above; real chapter rendering, narration and subjective QA remain unverified.

Preliminary cost example, **not a measured estimate for this unknown chapter**:
assuming 200,000 Flash input tokens, 40,000 output/thinking tokens and 20 minutes
of TTS, current listed generation charges are about **$0.30 text/vision + $0.27
audio**, plus TTS input, matching/embeddings, packaging and retries. A tentative
**$1–$3 estimate with $5 maximum authorized test budget** is reasonable only after
confirming panel count, script length, selected models and rates. No budget is
currently approved. Grounded external research and paid thumbnail image generation
are excluded. Present the measured source-based estimate and ask before enabling
any provider. Existing usage estimates are not a guaranteed provider billing cap;
keep the daily ceiling at the approved amount and record actual billing where
available. Stop rather than bypass caps. Uncertain failed/retried-call billing must
be reported separately from the app's successful-call estimates.

Official current pricing references: [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)
and [current model aliases](https://ai.google.dev/gemini-api/docs/latest-model).
Recheck the selected rate/model before approval. The October 2026 rates used above
are Flash $0.75/M input + $3.75/M output and Flash TTS $9/M audio output at 25
tokens/second. Do not use a commercial API just to check model availability.

## Stage evidence and evaluation protocol

All real chapter stages below are **not run**. There is no Chapter 34 project URL,
real narration, final MP4, QA verdict or measured chapter cost/time yet. Proposed
localhost addresses must not be presented as a working website.

| Stage | Capture on the actual isolated app | Evaluation |
|---|---|---|
| Source/ingest | Verified URL/identity, run ID, ordered page hashes, download logs/timing | Missing/duplicate/wrong chapter pages; original remains unchanged |
| Split/OCR | Panel paths/hashes/order, bubble/line/speaker evidence and uncertainty | Missing/reordered dialogue and incorrect attribution; human image comparison |
| Descriptions/Bible | Generation inputs/model/provenance, source-linked outputs/revisions | Art/text contradictions, unsupported canon, uncertain identity |
| Script | Source-review warnings, text/provenance, critique and revision history | Hallucinations/important omissions require human adjudication; deterministic linkage alone insufficient |
| Voice/matching | TTS config, text/hash, per-beat audio, durations and panel assignments | Naturalness/pronunciation human-reviewed; silence/clipping/repetition/timing measured where supported |
| Storyboard/edit | Before/after edit, changed beat/shot, original evidence links | User reads source and edits per-beat narration in existing UI; verify rerecording and saving, do not promise a new full-script editor |
| Render/QA | Exact final MP4 hash, media manifest, timestamped diagnostics | Decode/streams/peaks/silence technical checks; artistic pacing/visual choices human-reviewed |
| Targeted correction | Defect, affected subset, rerender logs, old/new final hashes and QA | Verify changed content and retained unaffected artifacts; old report must become stale after export changes |
| Current SEO/thumbnail | Existing truth card/metadata/concepts and their available evidence | Story relevance evaluated by owner; planned research/Intelligence engine remains unbuilt |
| Queue simulation | Local-only queue/schedule events, visible simulation label, zero uploader calls | Not implemented yet; real queue controls disabled until explicit simulation exists |

Record stage start/end, status, recoverable errors, model/call estimates and
outputs under the private staging run. Keep copyrighted art/dialogue, credentials
and private logs out of the public repository. Save a sanitized report to GitHub
and update memory/roadmap at each meaningful checkpoint. Preserve completed outputs
if a later stage blocks; never substitute invented/mock media for actual results.

Next independent safe task: complete a fixture-tested local-only queue simulation
and renderer dependency smoke verification, then launch only after owner approval.
Real-media calibration, semantic acceptance and legacy policy remain open.
