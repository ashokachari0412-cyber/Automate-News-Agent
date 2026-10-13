@echo off
title Personal AI Content Agent - Stop Docker Stack
echo Stopping all AI Content Agent containers...
cd /d "%~dp0"
docker compose down
echo.
echo All containers stopped cleanly.
pause
