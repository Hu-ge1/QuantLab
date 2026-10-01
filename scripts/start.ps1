$ErrorActionPreference = "Stop"

$appRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $appRoot "backend"
$logDir = Join-Path $appRoot "logs"
$healthUrl = "http://127.0.0.1:8000/api/health"
$appUrl = "http://127.0.0.1:8000"

function Test-QuantLab {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $health.app -eq "quant-lab"
    } catch {
        return $false
    }
}

if (Test-QuantLab) {
    Write-Host "QuantLab is already running. Opening browser..."
    Start-Process $appUrl
    exit 0
}

$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "Port 8000 is already in use. QuantLab cannot start." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    Write-Host "Python not found. Please install Python 3.10 or higher." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
$python = $pythonCommand.Source

& $python -c "import fastapi, uvicorn, httpx, akshare, pandas, numpy, pydantic" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "First run: installing backend dependencies..."
    & $python -m pip install -r (Join-Path $backendDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Dependency installation failed. Please check your network and retry." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stdoutLog = Join-Path $logDir "server.out.log"
$stderrLog = Join-Path $logDir "server.err.log"
$process = Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru
$process.Id | Set-Content -LiteralPath (Join-Path $logDir "server.pid") -Encoding ascii

Write-Host "Starting QuantLab..."
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-QuantLab) {
        Write-Host "QuantLab started: http://127.0.0.1:8000" -ForegroundColor Green
        Start-Process $appUrl
        exit 0
    }
}

Write-Host "QuantLab failed to start. Error log: $stderrLog" -ForegroundColor Red
if (-not $process.HasExited) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath (Join-Path $logDir "server.pid") -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $stderrLog) {
    Get-Content -LiteralPath $stderrLog -Tail 30
}
Read-Host "Press Enter to exit"
exit 1
