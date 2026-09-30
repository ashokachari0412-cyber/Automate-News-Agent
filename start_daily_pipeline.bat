@echo off
title Personal AI Content Agent - Run 24h Scan & Listen
echo Scanning breaking 24h AI news and dispatching to Telegram...
cd /d "%~dp0"
python -m src.cli run --telegram
pause
