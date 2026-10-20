# Manhwa Recap Studio — Agent Ground Rules

## Rule 0 — Passive persistence (NON-NEGOTIABLE)
Every working session, regardless of IDE, agent, or machine:

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
3. Never let GitHub drift from deployed reality: anything deployed (e.g. via
   `railway up`) must be committed and pushed in the same session.

## Working directory
Use `~/dev/manhwa` (off-iCloud clone). The copy at `~/Desktop/manhwa` lives on
iCloud-synced Desktop and randomly hangs git/file operations (dataless-file
materialization) — do not rely on it.

## Where things live
- History / decisions: `manhwa-recap-v1/memory.md` (append-only log, read first)
- Backend + review UI: `manhwa-recap-v1/review_ui/` (FastAPI `server.py`,
  `ingest.py`, `usage.py` cost guardrails, `static/index.html` frontend)
- Pipeline: `panel-split/` (panel extraction), `panel-describe/` (Gemini vision),
  `manhwa-recap-v1/` (narrate, matcher, TTS, segments, hyperframes render)
- Deploy: Railway backend (`railway up` from this clone; `deploy/Dockerfile`),
  Vercel frontend (`npx vercel@latest --prod` from `review_ui/static/`),
  live at https://manhwa.nodepilot.dev (Basic Auth) → proxies to
  https://recap-studio-production.up.railway.app (x-shared-secret header)

## Safety rails
- Cost guardrails in `review_ui/usage.py` gate every Gemini/TTS call — never
  bypass them; raise caps via env vars on Railway, not by deleting checks.
- Secrets are Railway/Vercel env vars. Never print secret values into command
  output or commit them.
- Content is scraped from unlicensed aggregators: fine for internal R&D only;
  rights/source-policy gating (Stage 7) is required before anything public.
