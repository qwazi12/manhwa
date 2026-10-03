# Chapter Autopilot — runbook & config

Ingests new chapters of every Tracker (watchlist) series on its own, one at a
time, so they land in **Projects → Ready for review**. Approving on the Board
renders and exports, as before. Code: `manhwa-recap-v1/review_ui/autopilot.py`
(decisions), `project_archive.py` (archive), wiring in `server.py`
(`# AUTOPILOT` section). State: `projects/_autopilot.json` on the volume.

## Config

| Setting | Value | Where |
|---|---|---|
| On/off | **OFF** by default | Ingest page → 🤖 Autopilot card, or `POST /api/autopilot/settings {"enabled":true}` |
| Chapters per day | 4 (0–20) | card → Today → save |
| Day | America/New_York, same day as the spend cap | `autopilot.et_day` |
| Engine | Gemini only | `DEFAULTS["engine"]` |
| Start window | each series' latest 3 chapters, fixed the first time the series is seen | `settings.window`, `start_from` in state |
| Order | round robin: series with the fewest autopilot-made chapters first, ties by watchlist order (tier, then rank) | `autopilot.pick` |
| Check cadence | every 10 min (`TICK_SECONDS`), first pass 60 s after boot | `server._scheduler_loop` |
| Source re-read | every 6 h (`REFRESH_EVERY`), or ↻ check now | free (no AI calls) |
| Budget | starts a chapter only if today's spend + one chapter's estimate ≤ `MAX_DAILY_SPEND_USD` | Railway env |
| Estimate | mean cost of the last 5 autopilot chapters; $0.80 until there are any | `autopilot.estimate` |
| Failures | 1st failure: retried after 1 h. 2nd: series blocked ("needs you") until ↻ retry | `FAIL_LIMIT`, `FAIL_COOLDOWN` |
| Archive | a project with a published video is archived; its folder is deleted 14 days later unless **Keep** | `project_archive.ARCHIVE_DAYS` |
| Local/dev | `AUTOPILOT_SCHEDULER=0` disables the scheduler and restart-resume | env |

## Pause / stop / resume

- **Everything new**: card → ⏹ Switch off. Nothing new starts; a running chapter finishes its current step.
- **One series**: card → All series → ⏸ pause / ▶ resume.
- **One running chapter**: Logs → ⏸ pause, ▶ resume, ⏹ stop (lands between pipeline steps). A chapter you stopped is **not** picked again by itself; its series shows "stopped by you" until you press ▶ in Logs or ↻ retry on the card.
- **Resume anything that ended**: Logs → ▶ on a stopped / failed / cap-paused / restart-cut ingest re-queues it under the same record, reusing cached stages. For an approve-render, open that project first, then ▶.
- **Approve → render**: unchanged; ⏹ Stop on the board's render strip, ▶ resume from Logs.

## What happens on its own

- **Deploy / restart**: an ingest cut off in the last 6 h is marked `interrupted` and re-queued at startup (max 2 times). Older or owner-stopped ones are written off as before.
- **Spend cap hit**: the ingest becomes `budget_paused` (not an error) and is re-queued first after midnight ET.
- **Story check**: every finished ingest gets the free rule check; the count shows on its Projects row.
- **Never repeat**: every queued or finished chapter is in the ledger and is never re-made, even if its project is deleted. A manual ingest still works for any chapter.

## Where to look when something is wrong

- Card → "Waiting because", "Last check", red scheduler errors.
- Logs & Activity → rows tagged `autopilot`.
- Railway logs: JSON lines with `"service": "autopilot"` (every decision, settings change, archive and delete) and `[scheduler]` / `[boot]` lines.
- `GET /api/autopilot` → full state, every series' reason, last 8 autopilot chapters.
