@echo off
title Alfred AI Studio - Web App Orchestrator
echo Starting Alfred AI Web Application & Orchestrator Server...
cd /d "%~dp0"
python -m src.cli serve --host 0.0.0.0 --port 8000
pause
