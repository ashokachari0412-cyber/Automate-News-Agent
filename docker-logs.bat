@echo off
title Personal AI Content Agent - Container Logs
cd /d "%~dp0"
docker compose logs -f
pause
