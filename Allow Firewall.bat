@echo off
setlocal
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
set HTTP=8787
set HTTPS=8788
if exist config.json (
  for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$v=(Get-Content -Raw 'config.json' | ConvertFrom-Json).http_port; if($v){$v}else{8787}"`) do set HTTP=%%p
  for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$v=(Get-Content -Raw 'config.json' | ConvertFrom-Json).https_port; if($v){$v}else{8788}"`) do set HTTPS=%%p
)
netsh advfirewall firewall delete rule name="Sentry Room HTTP" >nul 2>&1
netsh advfirewall firewall delete rule name="Sentry Room HTTPS" >nul 2>&1
netsh advfirewall firewall add rule name="Sentry Room HTTP" dir=in action=allow protocol=TCP localport=%HTTP% profile=any
netsh advfirewall firewall add rule name="Sentry Room HTTPS" dir=in action=allow protocol=TCP localport=%HTTPS% profile=any
echo Allowed inbound TCP %HTTP% and %HTTPS%.
echo You can close this window.
pause
