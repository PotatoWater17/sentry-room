@echo off
setlocal
cd /d "%~dp0."
if not exist ".venv\Scripts\python.exe" (
  echo Run Launch Sentry.bat once first, then run this again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "from status_ui import install_login_start; print(install_login_start())"
if errorlevel 1 (
  echo Could not add Sentry Room to Windows startup.
  pause
  exit /b 1
)
echo.
echo Sentry Room will open about 20 seconds after you sign in to Windows.
echo No command window stays open. A status window opens instead.
echo Closing that window turns the camera off.
echo.
echo To stop this, delete Sentry Room.cmd from:
echo   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
echo.
pause
exit /b 0
