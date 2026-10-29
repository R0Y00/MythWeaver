@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\local.py" napcat-start
if errorlevel 1 pause
