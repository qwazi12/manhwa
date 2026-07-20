# Manhwa Recap Studio — review UI + pipeline in one container.
# Needs python, node (hyperframes), ffmpeg (render/concat), and chromium
# (hyperframes headless render). Serverless/Vercel can't host this — use a
# small VM (2 vCPU / 4 GB is comfortable) with Docker.
FROM python:3.13-slim

# PRODUCER_LOW_MEMORY_MODE=1: hyperframes' documented safe render profile —
# pins 1 worker, screenshot capture, no auto-worker calibration. Without it,
# "auto workers" sees the HOST's 48 cores on Railway and over-spawns Chrome
# (~256MB each) into the container's much smaller cgroup limit.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PRODUCER_LOW_MEMORY_MODE=1

# system deps: ffmpeg + node + fonts + the full Chrome-for-testing shared-lib
# set (hyperframes downloads its own Chrome; Debian's `chromium` package is NOT
# in its discovery list, so the distro browser was never used — see Session 19)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates gnupg fonts-dejavu-core unzip \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 \
      libcups2 libdbus-1-3 libdrm2 libexpat1 libgbm1 libglib2.0-0 \
      libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
      libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Lean, explicit deps — the review UI + ingestion need only these. (No torch /
# sentence-transformers: the matcher uses Gemini embeddings, not local models.)
RUN pip install --no-cache-dir \
      "fastapi" "uvicorn[standard]" "python-multipart" "pydantic>=2" \
      "google-genai>=1.0.0" "numpy" "Pillow" "certifi" \
    && pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir "ultralytics"

COPY . /app
# Reassemble the YOLO panel-detector weights (119MB — too big for one git
# file, stored as <100MB chunks in panel-split/weights/; the joined .pt is
# gitignored). Without this the splitter SILENTLY fell back to geometric
# splits in production (268 vs 126 crops on dungeon-odyssey ch1). The sha256
# assert makes a corrupt/partial join fail the BUILD, not the ingest.
RUN cat panel-split/weights/yolo_panel_detector.pt.part-* > panel-split/yolo_panel_detector.pt \
    && python3 -c "import hashlib;h=hashlib.sha256(open('panel-split/yolo_panel_detector.pt','rb').read()).hexdigest();assert h=='566e9ff59ec146afb695eb48a8aaae983b43bb72c3a57f63afafe12f3b349af4', h"
# S1b figure-anchor model (generic COCO person detector, ~6MB): baked at
# build time so the splitter never downloads weights at runtime.
RUN curl -fsSL -o panel-split/yolov8n.pt \
      https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt \
    && python3 -c "import os;assert os.path.getsize('panel-split/yolov8n.pt') > 5_000_000"
# hyperframes CLI (pinned via npx at runtime; warm the cache), then bake its
# Chrome-for-testing into the image so renders never download at runtime
# (the runtime download inside the container ended in "Browser not available").
# The cached binary is wrapped with --no-sandbox: the container runs as root,
# and Chrome refuses to start sandboxed as root — the other half of that error.
RUN cd manhwa-recap-v1/review_ui && npx --yes hyperframes --version || true
RUN npx --yes hyperframes browser ensure \
    && CHROME="$(npx --yes hyperframes browser path | tr -d '\r' | grep -E '^/' | tail -1)" \
    && echo "wrapping: $CHROME" \
    && mv "$CHROME" "$CHROME.real" \
    && printf '#!/bin/sh\nexec "%s.real" --no-sandbox "$@"\n' "$CHROME" > "$CHROME" \
    && chmod +x "$CHROME" \
    && "$CHROME" --version

EXPOSE 8000
# Make entrypoint script executable
RUN chmod +x /app/deploy/entrypoint.sh

# secrets provided at runtime via env (GEMINI_API_KEY, TTS_API_KEY) or an .env
WORKDIR /app/manhwa-recap-v1/review_ui
CMD ["/app/deploy/entrypoint.sh"]
