#!/usr/bin/env bash
# ==============================================================================
# Stop Multi-Container Stack (Linux / macOS / VPS / Cloud)
# ==============================================================================
set -e

echo "🛑 Stopping all Personal AI Content Agent services..."
docker compose down

echo "✅ All containers stopped cleanly."
