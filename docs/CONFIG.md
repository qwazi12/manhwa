# Recap Studio — CONFIG

What the studio runs, when, and every setting that changes its behaviour. Kept in step with the code (global rule 33); if this file and the code disagree, the code is right and this file is a defect.

Last checked against the code: 2026-10-04, commit after 6c22a4f. Entries marked "Added 2026-11-13" were checked on the branch `feat/audit-fixes-20261113`; the rest of this file was not re-verified then (docs/AUDIT_2026-11-13.md lists known gaps).

## Services

| Part | Where | Deploys |
|---|---|---|
| Backend (FastAPI, pipeline, scheduler) | Railway service `recap-studio`, volume at `/app/data` | every push to `main` (restarts the server; check running jobs first) |
| Frontend (Next.js, `manhwa-recap-v1/web`) | Vercel project `manhwa-studio`, Root Directory `manhwa-recap-v1/web`, framework Next.js | every push to `main` |
| Site | https://manhwa.nodepilot.dev (Basic Auth) | — |
| Classic pages | `/storyboard`, `/review` (forwarded to Railway) | with the backend |

## Flows

| Flow | Starts from | Steps |
|---|---|---|
| Chapter | paste box (Home/Library), autopilot, Library → Make | scrape → split → describe (+ image check, + cast research if the series has none) → narrate (+ script editor pass) → voice → match → crops → segments |
| Render | Chapter → **Render video** | re-voice if the studio voice changed → clips → export (−14 LUFS, speed) → Google Drive copy → free clips |
| Post | Chapter → Video → **Approve & schedule** (or Post now) | queue row → next free slot (schedule) or Post now → Upload-Post → YouTube confirms → Posted → archived 14 days later |
| Re-run a step | Chapter → step strip → ↻ Redo | clears that step and everything after it, re-queues the chapter |

## Schedules (all times America/New_York)

The scheduler checks every **10 minutes** (first check 60 s after boot); `AUTOPILOT_SCHEDULER=0` switches it off (local/dev).

| Step | When | What |
|---|---|---|
| budget | each check | resumes chapters paused by the daily spend cap once the day rolls over |
| archive | each check | archives posted chapters; deletes archived ones after 14 days unless **Keep** |
| posting | each check, when the schedule is **on** | at each set time (default 12:00, 18:00; live: 11:00, 19:00), posts the next scheduled video; at most N per channel per day (default 1); slots that passed before switching on are skipped that day |
| demand | weekly | YouTube recap demand per series (suggestions only) |
| new sources | each check | reads any series source never checked yet |
| SEO research | when SEO runs | per series, cached 7 days in `projects/_seo_web/`: a Google-grounded web search (official English title, other names people search, author/artist, platform, genres, keywords, graded sources) + a YouTube search for "<series> manhwa recap"; ≈ 2 Gemini calls per series a week + 200 YouTube quota units per SEO run. "Re-read the channel too" refreshes it |
| publish prep | each check | up to 5 chapters waiting for you that lack SEO (≈ $0.012 each, only with budget left) or a thumbnail (free) get them. New chapters get SEO when making finishes; new videos get a thumbnail, then the Drive copy, after the render |
| autopilot | each check, when **on** | makes the next chapter: first the top ready entry of your **Make next** list (Library), else round robin; N per day; re-reads every series source every 6 h |
| export cleanup | on export listing | deletes exports older than 7 days, except videos still scheduled to post |

Added 2026-11-13 (branch `feat/audit-fixes-20261113`):

| Step | When | What |
|---|---|---|
| source check | each check, every 6 hours, **whether or not autopilot is on** | reads every series page for new chapters (free) |
| account hold | after an AI-provider account failure (out of credits, bad key) | autopilot starts nothing for 30 minutes, says why, then tries one chapter; no chapter is struck for it |

Production Python packages are pinned in `deploy/requirements.txt` (the image used to install the newest release of each on every rebuild).

Read-only reports: `GET /api/spend` (now also by stage, thinking tokens, Flex requested vs served), `GET /api/scorecard` (quality, corrections, cost, release timeliness; kept 5 minutes, `?fresh=1` recomputes).

## Settings changed in the app (stored on the volume)

| Setting | Where in the app | File |
|---|---|---|
| Autopilot on/off, chapters per day, daily budget | Settings → Autopilot, sidebar switch | `projects/_autopilot.json` |
| Narrator voice and style (studio default) | Settings → Narrator voice | `projects/_voice_default.json` |
| Channels, default privacy, posting schedule, export speed | Settings | `projects/_studio_settings.json` |
| Posting queue | Queue | `projects/_post_queue.json` |
| Series list, priority (Make now / Next up / Watching) | Library | `projects/_watchlist.json` |
| Cast lists (Series Bible) | Library → Cast | `projects/_series_bibles/` |
| Demand research | Library cards | `projects/_demand.json` |

## Environment variables (names only — values live in Railway / Vercel)

**Railway (backend)**

| Name | Purpose |
|---|---|
| `SHARED_SECRET` | the frontend must send it; production refuses to start without it |
| `GEMINI_API_KEY` | describe, script, research, SEO, voice (Gemini) |
| `ANTHROPIC_API_KEY` / `Claude_API_KEY` | Claude lab and story check |
| `UPLOAD_POST_API_KEY` (+ `UPLOAD_POST_PROFILE`) | posting |
| `YOUTUBE_API_KEY` | demand research, views |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Drive copy (omnistream-bot) |
| `Flamingo_Remix_DRIVE_FOLDER_ID` (or `DRIVE_FOLDER_ID`) | Shared Drive folder for copies |
| `MAX_DAILY_SPEND_USD` | hard daily stop for the whole site (live: $10); Settings → Spending can change it, up to the ceiling below |
| `MAX_DAILY_SPEND_CEILING_USD` | the most Settings may raise the daily limit to (default $25) |
| `MAX_DAILY_GEMINI_CALLS`, `MAX_DAILY_TTS_CHARS`, `MAX_DAILY_CLAUDE_CALLS`, `MAX_*_PER_JOB` | per-day and per-chapter call caps |
| `PIPELINE_MODEL` (default `gemini-3.8-flash`), `AUTOPILOT_TIER` (default `flex`), `RECAP_SERVICE_TIER` | model and price tier |
| `EXPORT_SPEED` | overrides the Settings speed when set |
| `EXPORT_LUFS` | loudness target (default −14; `off` to skip) |
| `EXPORT_RETENTION_DAYS` | export cleanup (default 7) |
| `THUMB_COVER_TRIM_TOP`, `THUMB_COVER_TRIM_BOTTOM` | share of the scraped series cover cut off the top / bottom edge before it's used in a thumbnail, to remove aggregator watermarks (defaults 0.07 / 0.05) |
| `SPLIT_GUTTER_ROW_STD` | splitter flat-gutter rule (default 6; 0 = off) |
| `AUTOPILOT_SCHEDULER` | `0` switches the background scheduler off |
| `CACHE_RELEASE` (`off` disables), `CACHE_RELEASE_SECONDS` (300) | while idle, drop the Linux file cache renders leave behind — Railway bills it as memory (review_ui/cache_release.py) |
| `AUTO_FRAMING` (`off` disables), `AUTO_TURN` (`suggest` default / `apply` / `off`), `AUTO_TURN_MIN_CONF` (0.8), `AUTO_FRAMING_MODEL` (`gemini-3.8-flash`), `AUTO_FRAMING_WORKERS` (5) | automatic subject framing and sideways-picture turns (docs/FLAMINGO_AUTO_FRAMING.md) |
| `VISION_THINKING_LEVEL` (default `low`), `SCRIPT_THINKING_LEVEL` (default: the model's own), `CROP_THINKING_BUDGET` (default `0`) | how much the model may "think" (billed as output). `default` / empty restores the model's own setting. Added 2026-11-13 |
| `FLEX_TRIP` (3), `FLEX_COOLDOWN_S` (600) | after this many Flex refusals in a row, Flex is not requested for this many seconds |
| `CACHE_CODE_STRICT` (`1` = old rule) | by default a change to code files alone does not make finished describe/script/voice work "stale" |
| `CRITIQUE_VERIFY` (`0` disables) | rewritten script units are re-checked once by the script editor |
| `RELEASE_GATE` (`off` default / `critical` / `strict`) | whether script and final-media findings block approval and posting (`release_gate.py`). `off` only reports them in `/api/scorecard`. A check that is missing, unreadable, unfinished or about another version of the file is never a pass |
| `RELEASE_GATE_LEGACY` (`block` default / `review`) | videos made before the checks existed have no evidence. `block`: missing evidence is critical. `review`: it is a review item (blocks only under `strict`). Unreadable or outdated evidence is critical under both |
| `MEDIA_QA_ENFORCE` (`1` enables) | final-media QA must pass, and the exact export be re-approved, before posting (not set in production) |
| `STITCH_ENGINE` (`clean` default / `copy`) | how merged videos are joined; `copy` is the join used before 2026-11-13 |
| `AUTO_APPEND_MODE` (`cumulative` default / `fresh`) | how the next merge is cut. `cumulative`: the last posted video plus the new chapters (posted 1-4 + chapter 5 → 1-5; the posted video is re-used as the first piece). `fresh`: the 2026-11-08 rule — start after the last posted video, no overlapping ranges. An unknown value is refused |
| `AUTO_APPEND_MIN` (1 when cumulative, 7 when fresh), `AUTO_APPEND_HOLD` | new chapters a merge needs; `series:chapter` pairs that post as singles |
| `USAGE_RESERVE_TTL_S` (1800) | how long money held for a running paid call counts against the daily cap if its process died without settling |
| `SHEET_GATE_OFF` (`1` disables) | the Posting Review sheet gate (also `POST /api/sheet_gate {"on": false}`) |
| `INGEST_LINE_MAX` (2), `JOB_STALL_SECONDS` (1800), `JOB_STOP_GRACE_SECONDS` (120) | chapters in line at once (one runs, one waits); when a silent render job is retired |
| tuning groups `SPLIT_*`, `CROP_*`, `BUBBLE_*`, `VALIDATOR_*`, `PLUS_*`, `LAB_*`, `CLAUDE_TEST_*`, `TTS_*`, `DESCRIBE_*` | defaults in code; change only with a measured reason |

**Vercel (frontend)**

| Name | Purpose |
|---|---|
| `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD` | site login (the /login page sets a 90-day signed cookie per device); on production a missing one closes the site (503) |
| `SHARED_SECRET` | added to every backend request; never sent to the browser |
| `BACKEND_URL` | optional; defaults to the Railway URL |
