@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请把此文件放在原平台目录，与 start-platform.bat 放在一起。
  pause
  exit /b 1
)
where git >nul 2>nul
if errorlevel 1 (
  echo 请先安装 Git for Windows，再双击此文件。现有平台没有更改。
  pause
  exit /b 1
)
set "MYTHWEAVER_GIT_BOOTSTRAP=%TEMP%\mythweaver-connect-%RANDOM%-%RANDOM%.py"
echo 正在下载 Git 接入工具；配置和游戏数据保留在原目录。
powershell.exe -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/R0Y00/MythWeaver/main/scripts/adopt_git.py' -OutFile $env:MYTHWEAVER_GIT_BOOTSTRAP"
if errorlevel 1 (
  echo 下载失败，尚未替换旧代码。请检查网络后重新运行。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "%MYTHWEAVER_GIT_BOOTSTRAP%" --root "%~dp0." %*
set "MYTHWEAVER_GIT_RESULT=%ERRORLEVEL%"
if not "%MYTHWEAVER_GIT_RESULT%"=="0" pause
exit /b %MYTHWEAVER_GIT_RESULT%
