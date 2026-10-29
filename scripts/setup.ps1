param(
    [string]$PythonExe,
    [string]$PostgresBin,
    [string]$PostgresZip,
    [string]$NapCatScript,
    [string]$BotId = '0',
    [string]$PipIndexUrl = 'https://pypi.tuna.tsinghua.edu.cn/simple',
    [string]$PipFallbackIndexUrl = 'https://mirrors.aliyun.com/pypi/simple/',
    [ValidateRange(1024,65535)][int]$ApiPort = 8001,
    [ValidateRange(1024,65535)][int]$DbPort = 55432,
    [switch]$RebuildFrontend
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
if ($BotId -notmatch '^\d{1,25}$' -or $ApiPort -eq $DbPort -or $ApiPort -eq 8080 -or $DbPort -eq 8080) { throw 'Use a numeric BotId and distinct Web/database ports; 8080 is reserved for OneBot.' }
foreach ($sourceUrl in @($PipIndexUrl,$PipFallbackIndexUrl)) {
    if (-not $sourceUrl) { continue }
    $sourceUri = $null
    if (-not [Uri]::TryCreate($sourceUrl,[UriKind]::Absolute,[ref]$sourceUri) -or $sourceUri.Scheme -ne 'https' -or $sourceUri.UserInfo -or $sourceUri.Query -or $sourceUri.Fragment) {
        throw 'Package indexes must be HTTPS URLs without credentials, query strings or fragments.'
    }
}
if (-not $PipIndexUrl) { throw 'PipIndexUrl cannot be empty.' }

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable (exit $LASTEXITCODE)" }
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $candidateArgs = @()
    if (-not $PythonExe) {
        if (Get-Command py.exe -ErrorAction SilentlyContinue) { $PythonExe = 'py.exe'; $candidateArgs = @('-3') }
        elseif (Get-Command python.exe -ErrorAction SilentlyContinue) { $PythonExe = 'python.exe' }
        else { throw 'Install Python 3.12+ x64 first, or pass -PythonExe C:\path\python.exe.' }
    }
    Invoke-Checked $PythonExe ($candidateArgs + @('-c','import sys,struct; assert sys.version_info >= (3,12) and struct.calcsize(chr(80))==8'))
    Invoke-Checked $PythonExe ($candidateArgs + @('-m','venv',(Join-Path $projectRoot '.venv')))
}
Invoke-Checked $venvPython @('-c','import sys,struct; assert sys.version_info >= (3,12) and struct.calcsize(chr(80))==8')
$pipArgs = @('-m','pip','--isolated','install','--disable-pip-version-check','--timeout','20','--retries','1','-r',(Join-Path $projectRoot 'requirements.lock'))
$selectedIndex = $PipIndexUrl
Write-Host "Installing Python dependencies from $selectedIndex"
& $venvPython @pipArgs '--index-url' $selectedIndex
if ($LASTEXITCODE -ne 0 -and $PipFallbackIndexUrl -and $PipFallbackIndexUrl -ne $selectedIndex) {
    $selectedIndex = $PipFallbackIndexUrl
    Write-Host "Primary index failed. Retrying with $selectedIndex"
    & $venvPython @pipArgs '--index-url' $selectedIndex
}
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed. Rerun with -PipIndexUrl https://pypi.org/simple or another HTTPS mirror.' }
New-Item -ItemType Directory -Path (Join-Path $projectRoot '.runtime') -Force | Out-Null
@{ pip_index_url = $selectedIndex } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot '.runtime\package-sources.json') -Encoding UTF8

if ($RebuildFrontend -or -not (Test-Path -LiteralPath 'apps\web\dist\index.html')) {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Frontend build needs Node.js 20.19+ or 22.12+. Release packages include a prebuilt frontend.' }
    Push-Location -LiteralPath 'apps\web'
    try { Invoke-Checked 'npm.cmd' @('ci'); Invoke-Checked 'npm.cmd' @('run','build') } finally { Pop-Location }
}

if ($PostgresZip) {
    $pgTarget = Join-Path $projectRoot '.runtime\postgresql'
    if (Test-Path -LiteralPath $pgTarget) { throw 'PostgreSQL program directory already exists. Preserve it; do not extract over it.' }
    New-Item -ItemType Directory -Path $pgTarget -Force | Out-Null
    Expand-Archive -LiteralPath $PostgresZip -DestinationPath $pgTarget
}
$setupArgs = @((Join-Path $PSScriptRoot 'local.py'),'setup','--bot-id',$BotId,'--api-port',"$ApiPort",'--db-port',"$DbPort")
if ($PostgresBin) { $setupArgs += @('--postgres-bin',$PostgresBin) }
if ($NapCatScript) { $setupArgs += @('--napcat-script',$NapCatScript) }
if (Test-Path -LiteralPath '.env') { Write-Host 'Existing .env preserved. New port/path/QQ arguments apply only to a fresh installation; edit .env to change existing settings.' }
Invoke-Checked $venvPython $setupArgs
Write-Host 'Ready. Password: .runtime\credentials.txt. Start: start-platform.bat.'
Write-Host 'QQ is optional: configure ONEBOT_BOT_ID/NAPCAT_SCRIPT in .env, then run local.py configure-napcat after logging in to NapCat once.'
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) { Write-Host 'Node.js not found: Node plugins are unavailable until Node.js is installed. Python plugins and the prebuilt UI remain available.' }
