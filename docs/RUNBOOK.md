# Recap Studio — RUNBOOK

How to pause, resume, re-run, read the logs, and recover from the usual failures. Settings and schedules are in [CONFIG.md](CONFIG.md); the pipeline itself in `manhwa-recap-v1/PIPELINE_HANDBOOK.md`.

## Pause and resume

| What | How |
|---|---|
| Stop everything made automatically | sidebar footer → **Autopilot** → tap twice. Settings → Autopilot shows the same switch. |
| Stop automatic posting | Settings → Posting schedule → **Turn off**. Scheduled videos stay in the Queue. |
| Stop one running job | jobs bar (top of every page) → **■ Stop** (tap twice) or ⏸ to pause. |
| Resume a stopped / budget-paused / restart-interrupted job | jobs bar or Activity → Jobs → **▶ Resume**. Cached work is reused. |
| Pause one series | Library → the series → **⏸ Pause**. |
| Stop the scheduler entirely (emergency) | Railway variable `AUTOPILOT_SCHEDULER=0`, redeploy. |

## Re-run

| What | How | Cost |
|---|---|---|
| One step of a chapter | Chapter → step strip → **↻ Redo** (describe / script / voice / timeline) | ≈ $0.45 / $0.24 / $0.14 / free |
| Render and export again | Chapter → **Render again** (joins the render queue) | free (CPU); the sheet offers a paid re-voice only when the studio voice changed (≈ $0.15–0.25) |
| Choose which chapters autopilot makes next | Library → a series → **Chapters** → tick → **＋ Add to Make next**. The **Make next** card at the top of Library shows the order (↑ ↓ ⤒ ✕). Made chapters leave the list; a failing one is skipped. Same daily limit and budget | ≈ $0.50 each |
| Take a scheduled or failed video back for another look | Posting schedule → Scheduled or Errors → **↩ Back to review**: off the posting queue, back to Home → Needs you; approve again to re-schedule | — |
| Render several chapters | Library → All chapters → tick → **🎬 Render selected**. One at a time, in order, each in its own voice. Progress: jobs bar and Activity → **Renders**; ✕ takes a waiting one out, ■ Stop stops the running one. | free |
| A chapter from scratch | paste the chapter link → Options → **Make it again from scratch** | ≈ $0.50 |
| A failed post | Posting schedule → Errors → **↻ Try again** | — |
| Copy to Drive again | Chapter → **Copy now** | free |
| Cast list | Library → Cast → **Research again** | ≈ $0.05 |

## Logs

- **Activity → Live**: every event (ingest stages, renders, posts, research, Drive, scheduler errors), filter and search.
- **Activity → Renders**: the render queue (rendering, waiting, finished with errors), with progress bars.
- **Activity → Jobs**: running, waiting, today, earlier, with cost.
- **Activity → Spend**: today against the cap, by day, by provider, by chapter. Google billing is the ground truth.
- Settings → Connection: deployed commit, last scheduler check, last scheduler error.
- Railway logs: `railway logs` (server output, tracebacks).

## Failures

| Symptom | Cause | Fix |
|---|---|---|
| Chapter shows **Waiting (budget_paused)** | a daily limit was reached (spend, AI calls `MAX_DAILY_GEMINI_CALLS` = 12000 live, or voice characters `MAX_DAILY_TTS_CHARS`); Settings → Spending shows all three with today's use | it resumes by itself as soon as there is room (raise the spend limit in Settings, or the call/character limits on Railway) or at midnight ET; autopilot waits for it before starting another |
| Chapter **Failed** at scrape | source blocked or the link changed | open the source link; paste the current link again |
| Narration on thin strips of one picture | splitter cut a full-bleed page (fixed 2026-10-04 by the flat-gutter rule) | Chapter → ↻ Redo **describe** |
| "No description was produced" in Issues | a describe call failed | Issues → **Re-run description** on that row |
| Post failed "already published" | the video was posted elsewhere | nothing to do; the row settles as Posted |
| Post failed, no channel / not approved | channel disconnected or video not approved | Settings → Channels; Chapter → Video → Approve & schedule |
| Drive: "no storage of its own" | folder not in a Shared Drive or robot not a member | add omnistream-bot as Content manager of the Shared Drive |
| Site shows "Site configuration missing" | a Vercel variable is gone | set `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD`, `SHARED_SECRET` on Vercel, redeploy |
| Every page shows 401 from the backend | `SHARED_SECRET` differs between Vercel and Railway | set the same value on both |
| Render failed "…budget is left. Nothing was changed" | the studio voice changed and the re-voice wouldn't fit today's cap | Render in the chapter's own voice (free), or re-voice after midnight ET |
| Board says **View only** | another chapter is rendering (renders read the open chapter) | wait for it; **Try again** on the banner |
| Signed out on a phone | sign-in cookie expired (90 days) or the password changed | sign in at /login |
| Job stuck with no progress | a stalled worker | Stop it in the jobs bar, then Resume |

### Added 2026-11-13 (branch `feat/audit-fixes-20261113`)

| What you see | What it means | What to do | Undo |
|---|---|---|---|
| Autopilot: "holding ~N min: the AI provider refused the account" | Gemini refused the account (credits ran out, key invalid), not a chapter. Nothing starts for 30 minutes; no chapter is marked failed for it | top up / fix the key in AI Studio; autopilot then tries one chapter by itself | the hold ends by itself, or when any chapter finishes |
| A series "blocked" with "cache is stale / edited … work preserved" after one failure | the same inputs would fail the same way, so it was not retried | Chapter → step strip → ↻ Redo that step (costs that step again), then Retry | — |
| A merge fails with "pieces are out of order or repeated", "encoded differently" or "add up to" | the compilation check refused to write a broken video; the previous file is untouched | rebuild the named chapter's video, then build the merge again | `STITCH_ENGINE=copy` uses the old join (the checks still run) |
| Log line "[stitch] clean join not possible … using the packet-copy join" | a piece could not be joined the new way; the old join was used so the merge still finished | nothing; the merged video will show timestamp warnings in final-media QA | — |
| A video shows "posting" long after a restart | the upload was cut off; its upload id is on record | press Post again: Upload-Post is asked first and the video is sent only if it never arrived | — |
| Posting blocked with "Release check (…)" | `RELEASE_GATE` is on and the video is blocked (a defect) or unknown (a check is missing, unreadable, unfinished, or about another version: `narration_changed`, `media_outdated`, `export_missing`, `component_changed`, `evidence_unreadable` …) | fix what is named. Lines edited on the board after the script check: re-run the script step, or approve the exact export (it then shows as a review item). Export replaced: re-run final-media QA. Archived export: its Drive record needs the checksum (re-archive) | `RELEASE_GATE=off`; `RELEASE_GATE_LEGACY=review` lets videos from before the evidence existed through as review items |
| A paid step stops with "the daily spend limit … would be exceeded (… spent + … for calls that ended without an answer + … held for calls running now …)" | spent, uncertain and held money together would pass the cap | wait for running calls, or raise the cap in Spend. Uncertain money stays counted until the day resets | holds of dead processes become uncertain after `USAGE_RESERVE_TTL_S`; at server start every leftover hold does |
| A paid step stops with "N paid calls in a row for job … ended without an answer" | the provider is timing out; each attempt may be billed | wait 15 minutes (`USAGE_UNCERTAIN_COOLDOWN_S`) or check the provider's status; other chapters are not held | — |
| A paid step stops with "the spend record cannot be written" | `counters.json` cannot be saved (disk full / read-only), so the cap cannot be enforced | free disk space; calls resume by themselves | — |
| Activity shows "N paid call(s) were running when the server restarted; $X is counted as uncertain spend" | a restart cut calls off; they may have been billed | compare with the provider's bill if the amount matters | — |
| Post fails with "an earlier attempt may already have reached Upload-Post and it could not be asked just now — nothing was sent" | an earlier attempt has no settled outcome and Upload-Post's status check is unreachable or answered something undocumented | press Post again later; nothing is uploaded until Upload-Post answers | — |
| A channel shows "unconfirmed … it is not known whether Upload-Post received the video" | the upload ended in a timeout, a dropped connection, a 5xx, or an empty / malformed answer. Its upload id is kept | press Post again: channels that already have the video are not sent it again; only those that provably failed or never arrived are uploaded | — |
| A channel shows "published" with `visibility: scheduled` | it is on YouTube as private and goes public at `public_at` | nothing | — |
| Auto-append built `a-n` starting at the posted video's first chapter | `AUTO_APPEND_MODE=cumulative` (default since 2026-11-13) | nothing — this is the rule | `AUTO_APPEND_MODE=fresh` + `AUTO_APPEND_MIN=7` restores the 2026-11-08 rule; Unmerge on the merge puts its pieces back |
| "Save as series defaults" reports "N approved, queued or posted video(s) kept their details" | those videos were signed off or have gone out; series defaults no longer rewrite them | edit that one video's details on its own page if it must change | — |
| `/api/spend` → `by_stage` shows "unlabelled" | calls logged before 2026-11-13 carry no stage | nothing | — |

Where to look: `/api/scorecard` for quality, corrections, cost and time-to-publish; `/api/spend` for cost by stage, thinking tokens and Flex served vs requested; `/api/autopilot` → `hold`.

## Deploy and roll back

- **Before every push** (code or docs): check nothing is running — Activity → Jobs, or `GET /api/jobsbar` returns `{"jobs":[]}`. A push restarts the backend; interrupted ingests resume once on boot, renders don't.
- CI (`.github/workflows/ci.yml`) runs the Python and web checks on every push and PR. Only push to `main` when it is green.
- Backend: push to `main` → Railway builds `deploy/Dockerfile`. Roll back: Railway → Deployments → redeploy the previous one, then revert the commit on GitHub (GitHub must match what runs).
- Frontend: push to `main` → Vercel builds `manhwa-recap-v1/web`. Roll back: `npx vercel rollback` (or Vercel → Deployments → Promote the previous one). Emergency return to the classic site: Vercel → Settings → Root Directory `.` and framework Other, redeploy.

## Memory and cost

Railway bills the container's memory **including the Linux file cache**. A render
reads/writes gigabytes, and with a 32 GB limit the kernel never drops that cache,
so billed memory used to sit at 2–4 GB for hours after a render. `cache_release.py`
drops the cache for the data folders every 5 minutes while nothing is running and
logs `[cache-release] idle: file cache … -> …` in the Railway logs. Turn it off with
`CACHE_RELEASE=off`. Check: `railway metrics --since 1d --memory` (idle should be
~0.2 GB).

## Backups

- Every rendered chapter is copied to the Flamingo Remix Shared Drive (`Series / Ch N`: video, thumbnail, script).
- Settings → Storage → **Download** gives a chapter's paid artifacts (descriptions, audio, script, segments) as a .tar.gz.
- Railway volume backups exist, but a restore replaces the **whole** disk — use it only for total loss.

## Secrets

Secrets live only in Railway and Vercel variables, never in the repo. If one is ever printed or committed: rotate it at the provider, update the variable on Railway/Vercel, redeploy. (2026-10-04: the Anthropic key was printed in an agent session — rotate `ANTHROPIC_API_KEY` / `Claude_API_KEY`.)

## Added 2026-11-14

| What you see | What it means | What to do | Undo |
|---|---|---|---|
| A merge is refused with "… is already being built" | another request is building the same release | wait for it | — |
| A merge is refused with "… is approved / in the posting queue / posted — it is not rebuilt over" | the release has been signed off or has gone out | send it back or Unmerge it first, then build | — |
| Packaging history shows `blocked` | an automatic step (series defaults, SEO fill-in, start-up title repair, thumbnail redraw) tried to change a video that is approved, queued, being posted, scheduled or posted; the change was dropped | to change it anyway, edit that video, or use `POST /api/series/defaults/retroactive` (preview first) | edit the video back; the history has every step |
| `GET /api/channels` → `ok: false` | `_channels.json` is invalid; publish defaults refuse to guess a channel | fix the listed problems | restore `_channels.json.prev` |
| Chapters are not made at the same time although `INGEST_WORKERS` > 1 | they are in the script or framing stage (one at a time), or one is paused / held | nothing | `INGEST_WORKERS=1` |
| Final-media QA of a compilation says "N of M lines not re-measured" | those chapters were cleaned up; their voice files were measured when each chapter was exported | nothing | — |

Storage clean-up (manual, never scheduled): `GET /api/storage/retention` lists what could go and why everything else is kept. `POST /api/storage/retention/apply {"categories": ["work_files"], "max_gb": 5}` moves items to `_trash/retention-<time>/`; to undo, move them back to the `path` shown. Emptying the trash (`"categories": ["trash"], "confirm": "purge"`) deletes for good. Refused while anything is running.

Where to look: `GET /api/release-calendar` for each chapter's path to publication and the 24–48 h target; `/api/spend` and `/api/logs/usage` for confirmed / estimated / uncertain / held money; `<chapter>/packaging_revisions.jsonl` for packaging history.

