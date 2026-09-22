@echo off
setlocal
title Change Sentry Room code
cd /d "%~dp0."
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Start Sentry Room once before changing the code.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py --change-code
echo.
pause
