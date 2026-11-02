@echo off
rem SpineBox: update the resident service (scheduled task). Double-click to run.
rem Asks for administrator rights, then runs deploy\update_spinebox.ps1.
rem Details: wiki/operations/server-setup.md
setlocal
net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_spinebox.ps1" -Mode Update %*
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (echo Done.) else (echo Failed with exit code %RC%.)
pause
exit /b %RC%
