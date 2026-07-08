#!/bin/sh
set -e

# If a persistent volume is mounted at /app/data, set up symlinks for mutable data
if [ -d "/app/data" ]; then
  echo "Persistent volume /app/data detected. Setting up symlinks..."
  
  # 1. segments-workspace
  mkdir -p /app/data/segments-workspace/clips
  mkdir -p /app/data/segments-workspace/exports
  if [ -d "/app/manhwa-recap-v1/hyperframes/segments-workspace" ] && [ ! -L "/app/manhwa-recap-v1/hyperframes/segments-workspace" ]; then
    echo "Migrating segments-workspace..."
    cp -r /app/manhwa-recap-v1/hyperframes/segments-workspace/* /app/data/segments-workspace/ 2>/dev/null || true
    rm -rf /app/manhwa-recap-v1/hyperframes/segments-workspace
  fi
  ln -sfn /app/data/segments-workspace /app/manhwa-recap-v1/hyperframes/segments-workspace

  # 2. review_ui/projects
  mkdir -p /app/data/projects
  if [ -d "/app/manhwa-recap-v1/review_ui/projects" ] && [ ! -L "/app/manhwa-recap-v1/review_ui/projects" ]; then
    echo "Migrating projects..."
    cp -r /app/manhwa-recap-v1/review_ui/projects/* /app/data/projects/ 2>/dev/null || true
    rm -rf /app/manhwa-recap-v1/review_ui/projects
  fi
  ln -sfn /app/data/projects /app/manhwa-recap-v1/review_ui/projects

  # 3. panel-split/review_crops
  mkdir -p /app/data/review_crops
  if [ -d "/app/panel-split/review_crops" ] && [ ! -L "/app/panel-split/review_crops" ]; then
    echo "Migrating review_crops..."
    cp -r /app/panel-split/review_crops/* /app/data/review_crops/ 2>/dev/null || true
    rm -rf /app/panel-split/review_crops
  fi
  ln -sfn /app/data/review_crops /app/panel-split/review_crops
  
  echo "Symlinks successfully mapped to persistent volume."
fi

# Run the backend FastAPI server
echo "Starting FastAPI review server..."
exec python -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8000}"
