@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Put connect-git.bat in the existing platform folder beside start-platform.bat.
  pause
  exit /b 1
)
where git >nul 2>nul
if errorlevel 1 (
  echo Install Git for Windows, then run this file again. No platform files were changed.
  pause
  exit /b 1
)
set "MYTHWEAVER_GIT_BOOTSTRAP=%TEMP%\mythweaver-connect-%RANDOM%-%RANDOM%.py"
echo Downloading the Git connection tool. Existing settings and game data will be preserved.
powershell.exe -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/R0Y00/MythWeaver/main/scripts/adopt_git.py' -OutFile $env:MYTHWEAVER_GIT_BOOTSTRAP"
if errorlevel 1 (
  echo Download failed. Existing code was not replaced. Check your connection and retry.
  pause
  exit /b 1
)
rem Parse the exit before Python runs: adoption may replace this batch file.
".venv\Scripts\python.exe" "%MYTHWEAVER_GIT_BOOTSTRAP%" --root "%~dp0." %* & if errorlevel 1 (pause & exit /b 1) else exit /b 0
