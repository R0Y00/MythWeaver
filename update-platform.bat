@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Parse the exit before Python runs: Git may replace this batch file.
".venv\Scripts\python.exe" "scripts\update.py" %* & if errorlevel 1 (pause & exit /b 1) else exit /b 0
