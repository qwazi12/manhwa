# Recap Review UI

A browser workstation for reviewing and (MVP-2) editing the recap, built over
the existing segment pipeline. It does **not** reimplement pipeline logic — it
serves `hyperframes/segments-workspace/segments.json` + the per-segment rendered
clips, and drives the same Python ops (`render_segments`, `matcher`, `narrate`,
`tts`, `speed_up`).

## Run

```bash
cd manhwa-recap-v1
./venv/bin/python -m uvicorn review_ui.server:app --port 8000 --app-dir .
# open http://localhost:8000
```

## MVP-1 — read-only review + approve (done)

- **Timeline** of segment cards (panel thumbnail, duration, status color).
- **Preview** plays the selected segment's rendered clip (HTTP Range → seeking).
- **Inspector**: panel image, full narration text, per-segment approve/reject.
  Keyboard: `A` approve, `R` reject, `←/→` prev/next.
- **State** in `review.json` (never mutates `segments.json`); survives restarts.
- **Export** concatenates ONLY approved clips, optional `1.5×` pass.
- **Render missing** renders any segment lacking a clip (`render_segments --only`).

### API
| Method | Route | Purpose |
|---|---|---|
| GET  | `/api/project` | manifest + status + counts + durations |
| GET  | `/clip/{i}` | segment clip MP4 (range-enabled) |
| GET  | `/thumb/{i}` | cached panel thumbnail |
| POST | `/api/segments/{i}/status` | set approved/rejected/pending |
| POST | `/api/export` | concat approved clips (`{"speed":1.0\|1.5}`) |
| POST | `/api/render-missing` | render segments without a clip |

## Deploy (later)

Deploy-agnostic: put uvicorn behind any reverse proxy and point a subdomain
(e.g. `manhwa.kymediamgmt.com`) at it — no code changes. Frontend is a static
SPA in `static/`, so it can also be served from a CDN with the API on a
separate host.

## MVP-2 — direct edits (next)

Panel swap (matcher top-K), narration edit (re-TTS one beat), re-time/hold,
reorder/split/merge, single-clip re-render with live progress, undo/versioning,
live in-browser HyperFrames preview.
