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

## Deploy and roll back

- **Before every push** (code or docs): check nothing is running — Activity → Jobs, or `GET /api/jobsbar` returns `{"jobs":[]}`. A push restarts the backend; interrupted ingests resume once on boot, renders don't.
- Backend: push to `main` → Railway builds `deploy/Dockerfile`. Roll back: Railway → Deployments → redeploy the previous one, then revert the commit on GitHub (GitHub must match what runs).
- Frontend: push to `main` → Vercel builds `manhwa-recap-v1/web`. Roll back: `npx vercel rollback` (or Vercel → Deployments → Promote the previous one). Emergency return to the classic site: Vercel → Settings → Root Directory `.` and framework Other, redeploy.

## Backups

- Every rendered chapter is copied to the Flamingo Remix Shared Drive (`Series / Ch N`: video, thumbnail, script).
- Settings → Storage → **Download** gives a chapter's paid artifacts (descriptions, audio, script, segments) as a .tar.gz.
- Railway volume backups exist, but a restore replaces the **whole** disk — use it only for total loss.

## Secrets

Secrets live only in Railway and Vercel variables, never in the repo. If one is ever printed or committed: rotate it at the provider, update the variable on Railway/Vercel, redeploy. (2026-10-04: the Anthropic key was printed in an agent session — rotate `ANTHROPIC_API_KEY` / `Claude_API_KEY`.)
