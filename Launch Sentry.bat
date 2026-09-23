@echo off
setlocal
title Sentry Room
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
where py >nul 2>&1
if errorlevel 1 (
  echo Install Python 3 from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo First launch installs the camera and audio libraries. This can take a few minutes.
  py -3.11 -m venv .venv
  if errorlevel 1 py -3 -m venv .venv
  if errorlevel 1 (
    echo Could not create the Python environment.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -c "import aiohttp, cv2, numpy, sounddevice, cryptography, imageio_ffmpeg" >nul 2>&1
if errorlevel 1 (
  echo Installing libraries...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Setup failed.
    pause
    exit /b 1
  )
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo pythonw.exe is missing from the Python install.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0status_ui.py"
exit /b 0
