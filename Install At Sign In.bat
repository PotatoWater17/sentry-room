@echo off
setlocal
cd /d "%~dp0"
if /i "%~1"=="/run" goto run

set "LAUNCH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Sentry Room.cmd"
> "%LAUNCH%" echo @echo off
>> "%LAUNCH%" echo cd /d "%~dp0."
>> "%LAUNCH%" echo call "%~f0" /run
if not exist "%LAUNCH%" (
  echo Could not add Sentry Room to Windows startup.
  pause
  exit /b 1
)
echo.
echo Sentry Room will open about 20 seconds after you sign in to Windows.
echo The camera and speakers are ready then. If this PC signs you in by itself,
echo that is right after it powers on.
echo.
echo Leave that window open. Closing it turns the camera off.
echo.
echo To stop this, delete:
echo   %LAUNCH%
echo.
pause
exit /b 0

:run
title Sentry Room
echo Waiting for Windows to finish signing in...
ping -n 21 127.0.0.1 >nul
call "%~dp0Start Sentry Room.bat"
exit /b %errorlevel%
