# Manhwa Recap Studio — review UI + pipeline in one container.
# Needs python, node (hyperframes), ffmpeg (render/concat), and chromium
# (hyperframes headless render). Serverless/Vercel can't host this — use a
# small VM (2 vCPU / 4 GB is comfortable) with Docker.
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PUPPETEER_SKIP_DOWNLOAD=1 \
    HYPERFRAMES_CHROMIUM=/usr/bin/chromium

# system deps: ffmpeg + node + chromium (+ fonts for captions)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates gnupg chromium fonts-dejavu-core \
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
# hyperframes CLI (pinned via npx at runtime; warm the cache)
RUN cd manhwa-recap-v1/review_ui && npx --yes hyperframes --version || true

EXPOSE 8000
# Make entrypoint script executable
RUN chmod +x /app/deploy/entrypoint.sh

# secrets provided at runtime via env (GEMINI_API_KEY, TTS_API_KEY) or an .env
WORKDIR /app/manhwa-recap-v1/review_ui
CMD ["/app/deploy/entrypoint.sh"]
