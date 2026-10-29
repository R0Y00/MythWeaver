@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1" %*
set "SETUP_EXIT=%ERRORLEVEL%"
if errorlevel 1 (
  echo Setup failed. Read the error above.
) else (
  echo Setup completed. Run start-platform.bat next.
)
pause
exit /b %SETUP_EXIT%
