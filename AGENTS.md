# Manhwa Recap Studio — Agent Ground Rules

## Rule 0 — Passive persistence (NON-NEGOTIABLE)
Every working session, regardless of IDE, agent, or machine:

0. **READ `manhwa-recap-v1/memory.md` FIRST, before doing anything else** — at
   minimum the last few entries. It is the only reliable record of what was
   last done and where the project stands; never assume state from the code,
   git log, or a previous conversation alone. Confirm the project's current
   state from it before taking any action.

1. **Log every significant action to `manhwa-recap-v1/memory.md`** — append-only,
   newest at the bottom. Inputs received, decisions made, code changed, deploys,
   verifications (with evidence), failures and their root causes. The file is the
   project's single source of history; any new session must be able to catch up
   from it alone.
2. **Commit and push to GitHub (`origin main`) at every natural checkpoint** —
   after each completed fix/feature/log entry, not just at session end. The owner
   may switch IDEs or lose connection at any moment; unpushed work is considered
   lost work. If a push is impossible, say so explicitly rather than silently
   carrying local-only state.
3. Never let GitHub drift from deployed reality. Every push to `origin main`
   auto-deploys BOTH the Railway backend and the Vercel frontend and restarts
   the server — so deploying IS pushing. Never run `railway up` or a manual
   Vercel deploy.
4. **Check live jobs before EVERY push.** A push restarts the server and kills
   any render, ingest or upload in flight. `GET
   https://recap-studio-production.up.railway.app/api/jobsbar?failed=1` with
   header `x-shared-secret` (Railway variable `SHARED_SECRET`, read via
   `railway variables --json`, never printed). If anything is
   running/queued/paused/in_queue: commit locally, wait, push when idle, and
   say so. After a push, confirm `railway deployment list` shows SUCCESS for
   the commit and `/health` returns 200.
5. **Big or multi-part requests: write the plan into memory.md and wait for the
   owner's approval before building.** Surgical changes only; root cause with
   evidence; a test for every fix.

## Working directory
Use `~/dev/manhwa` (off-iCloud clone). The copy at `~/Desktop/manhwa` lives on
iCloud-synced Desktop and randomly hangs git/file operations (dataless-file
materialization) — do not rely on it.

## Where things live
- History / decisions: `manhwa-recap-v1/memory.md` (append-only log, read first)
- Backend: `manhwa-recap-v1/review_ui/` (FastAPI `server.py`, `ingest.py`,
  `autopilot.py`, `watchlist.py`, `chapter_seo.py`, `thumbnail_studio.py`,
  `usage.py` cost guardrails). `static/index.html` is the legacy studio only.
- Frontend: `manhwa-recap-v1/web/` (Next.js) — the site at
  https://manhwa.nodepilot.dev. Check with `npx tsc --noEmit && npx next build`.
- Handbook: `manhwa-recap-v1/PIPELINE_HANDBOOK.md` (how every feature works;
  update it with each change).
- Tests: in `review_ui/`, files with `def test_` run under pytest, the rest as
  `python3 test_x.py` (print N/N passed). Use `MIRROR_DISCOVERY=0`. Tests never
  call real services.
- Pipeline: `panel-split/` (panel extraction), `panel-describe/` (Gemini vision),
  `manhwa-recap-v1/` (narrate, matcher, TTS, segments, hyperframes render)
- Deploy: automatic on push to `origin main` — Railway backend
  (`deploy/Dockerfile`; never exclude a folder the Dockerfile builds from in
  `.dockerignore`) and Vercel frontend. Live at https://manhwa.nodepilot.dev
  (Basic Auth) → proxies to https://recap-studio-production.up.railway.app
  (x-shared-secret header).
- Live data lives on the Railway volume (`/app/manhwa-recap-v1/review_ui/projects`);
  inspect it with `railway ssh`. Never commit anything from `projects/`, scraped
  art or evidence (public repo).

## Safety rails
- Cost guardrails in `review_ui/usage.py` gate every Gemini/TTS call — never
  bypass them; raise caps via env vars on Railway, not by deleting checks.
- Secrets are Railway/Vercel env vars. Never print secret values into command
  output or commit them.
- Content is scraped from unlicensed aggregators: fine for internal R&D only;
  rights/source-policy gating (Stage 7) is required before anything public.
