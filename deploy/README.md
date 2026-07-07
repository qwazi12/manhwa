# Deploying Recap Studio to manhwa.kymediamgmt.com

This app is **not serverless-deployable** — it shells out to `ffmpeg`, `node`
(hyperframes), and headless `chromium` to render video. It needs a real
container host (a small VM). Everything below is ready; the two steps only
**you** can do are marked 🔑 (they need your accounts).

## What you provide
- 🔑 **A host** — a small VM with Docker (DigitalOcean/Hetzner/Fly-machine/EC2;
  2 vCPU / 4 GB RAM is comfortable). Note its **public IP**.
- 🔑 **DNS** — in whatever manages `kymediamgmt.com`'s DNS (Cloudflare/registrar),
  add one record:

  | Type | Name     | Value            | Proxy |
  |------|----------|------------------|-------|
  | A    | `manhwa` | `<VM public IP>` | DNS-only (grey cloud, so Caddy can issue the cert) |

  → resolves `manhwa.kymediamgmt.com` to the VM.

## Deploy (on the VM)
```bash
git clone https://github.com/qwazi12/manhwa.git
cd manhwa/deploy
printf 'GEMINI_API_KEY=...\nTTS_API_KEY=...\n' > .env    # your keys
docker compose up -d --build
```
Caddy fetches an HTTPS cert automatically once the A record resolves. Open
**https://manhwa.kymediamgmt.com**.

## Notes
- **Auth:** none yet — anyone with the URL can use it. Before exposing it
  publicly, add HTTP basic-auth in the `Caddyfile` (`basicauth { user <hash> }`)
  or put it behind Cloudflare Access.
- **Persistence:** rendered clips/projects live in the container. For durability,
  mount a volume onto `/app/manhwa-recap-v1/hyperframes/segments-workspace` and
  `/app/manhwa-recap-v1/review_ui/projects`.
- **Rights/source policy** (Stage 7) is deliberately out of scope for now.
