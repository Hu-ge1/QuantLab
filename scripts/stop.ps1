$ErrorActionPreference = "Stop"
$healthUrl = "http://127.0.0.1:8000/api/health"

try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    if ($health.app -ne "quant-lab") {
        throw "QuantLab is not running on port 8000. Stop aborted."
    }
} catch {
    Write-Host "QuantLab is not currently running, or port 8000 is occupied by another program."
    exit 0
}

$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
$processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $processIds) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if ($processInfo -and $processInfo.CommandLine -match "uvicorn main:app --host 127\.0\.0\.1 --port 8000") {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
        $parentInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($processInfo.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parentInfo -and $parentInfo.CommandLine -match "uvicorn main:app --host 127\.0\.0\.1 --port 8000") {
            Stop-Process -Id $parentInfo.ProcessId -ErrorAction SilentlyContinue
        }
    }
}
Remove-Item -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) "logs\server.pid") -Force -ErrorAction SilentlyContinue
Write-Host "QuantLab has been stopped."
