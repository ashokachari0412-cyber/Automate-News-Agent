@echo off
title Personal AI Content Agent - Multi-Container Docker Stack
echo ========================================================
echo Starting Multi-Container AI News Agent (Docker Compose)
echo Services: API Orchestrator, Telegram Bot, Pipeline Scheduler, Redis
echo ========================================================

cd /d "%~dp0"
docker compose up -d --build

echo.
echo Containers started!
echo - API & Swagger Docs: http://localhost:8000/docs
echo - Telegram Bot: Listening for interactions
echo - Scheduler: Running 24h periodic news scans
echo - Redis Cache: Port 6379
echo.
echo Viewing live logs (Press Ctrl+C to detach without stopping)...
docker compose logs -f
pause
