#!/usr/bin/env bash
# ==============================================================================
# Start Multi-Container Stack (Linux / macOS / VPS / Cloud)
# ==============================================================================
set -e

echo "🚀 Building and starting Personal AI Content Agent services..."
docker compose up -d --build

echo "✅ Containers active:"
docker compose ps

echo "📍 Access API Documentation: http://localhost:8000/docs"
echo "📜 Tailing logs (Press Ctrl+C to detach)..."
docker compose logs -f
