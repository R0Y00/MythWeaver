@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing_python
echo Starting MythWeaver server console...
".venv\Scripts\python.exe" "scripts\console.py" %*
if errorlevel 1 goto failed
exit /b 0

:missing_python
echo Python environment is missing: .venv\Scripts\python.exe
goto failed

:failed
echo.
echo Startup failed. Check the error above and .runtime\api.log.
if /I "%~1"=="--no-ui" exit /b 1
pause
exit /b 1
