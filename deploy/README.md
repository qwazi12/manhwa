# Deploying Recap Studio (→ manhwa.nodepilot.dev)

**Vercel can't host this app.** It shells out to `ffmpeg`, `node` (hyperframes),
and headless `chromium`, runs multi-minute render/ingestion jobs in background
threads, and keeps mutable state on disk. That's the opposite of serverless, so
it needs a **container host**, not Vercel/Netlify. The good news: container
hosts are just as low-hassle and deploy straight from this GitHub repo.

Everything's ready. The only steps that need **your** account are marked 🔑.

---

## Option A — Render (recommended, push-button)

Uses `render.yaml` (repo root) + `deploy/Dockerfile`.

1. 🔑 Render dashboard → **New → Blueprint** → connect `github.com/qwazi12/manhwa`.
   It reads `render.yaml` and provisions the service + a 5 GB persistent disk.
2. 🔑 Set the two secrets when prompted: `GEMINI_API_KEY`, `TTS_API_KEY`.
3. First build takes a few minutes (installs chromium/ffmpeg). You get a URL
   like `recap-studio.onrender.com`.
4. 🔑 **Custom domain:** Service → Settings → Custom Domains → add
   `manhwa.nodepilot.dev`. Render shows a **CNAME target**.
5. 🔑 **DNS:** wherever `nodepilot.dev` is managed (Vercel DNS is fine — it can
   hold records that point elsewhere), add:

   | Type  | Name     | Value                      |
   |-------|----------|----------------------------|
   | CNAME | `manhwa` | `<the target Render shows>` |

   HTTPS is issued automatically. Done → `https://manhwa.nodepilot.dev`.

## Option B — Railway (also one-click)
New Project → Deploy from GitHub → it detects `deploy/Dockerfile` → add the two
env vars + a volume on the same mountPath → add the custom domain + CNAME as above.

## Option C — Any VM with Docker
`docker compose -f deploy/docker-compose.yml up -d --build` (includes Caddy for
auto-HTTPS); point an **A** record at the VM IP. See `deploy/docker-compose.yml`.

---

## Notes
- **Auth:** none yet — anyone with the URL can use it. Add HTTP basic-auth
  (Caddy) or the host's access control before sharing publicly.
- **Persistence:** the disk mount keeps `segments-workspace` (clips/segments)
  across restarts. Add a second mount for `review_ui/projects` if you want
  ingested projects to survive redeploys too.
- **Cost:** a small always-on instance (~$7–15/mo) — needed because renders are
  CPU/RAM heavy and can't be serverless.
- **Rights/source policy** (Stage 7) is intentionally out of scope for now.
