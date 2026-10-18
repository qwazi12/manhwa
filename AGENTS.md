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
2. **Commit and push development checkpoints to GitHub feature branches.**
   The owner explicitly requires passive GitHub saving and append-only memory
   logging. Never push or merge to `main`: it deploys production. Use
   `dev/flamingo-*` branches; both Vercel project configs disable Git deployments
   for that prefix. Verify those guards and Railway's main-only source before
   the first push. Never stage generated media, credentials or scratch evidence.
   If a push fails, record it and say so; do not silently leave work local-only.
3. **No deployments, live-data mutations, paid API calls, account changes or
   real publishing/scheduling without separate owner authorization.** Never run
   `railway up` or a manual Vercel deploy. Do not enable new publication blockers
   in production before real-media calibration and explicit owner approval.
4. **Production pushes are forbidden by the current roadmap.** If a later owner
   instruction authorizes a production release, check live jobs first: GET
   `https://recap-studio-production.up.railway.app/api/jobsbar?failed=1` with
   `x-shared-secret` (never printed). Any running/queued/paused/in_queue job means
   wait: a main push restarts the backend. Verify SUCCESS and `/health` after
   an authorized release. Feature-branch backup pushes must not deploy.
5. **The October 18 autonomous roadmap is approved for reversible offline
   development.** Follow `docs/FLAMINGO_DEVELOPMENT_ROADMAP.md` sequentially;
   log significant decisions/results and preserve the full vision in
   `docs/FLAMINGO_STUDIO_VISION.md`. Do not request approval again for ordinary
   in-scope engineering. Mark implemented milestones Ready for Review; do not
   call uncalibrated or undeployed work production-complete. Keep milestones
   in reviewable commits/branches. User restrictions override older main-push
   and plan-only instructions. Every meaningful fix needs a regression test.

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
