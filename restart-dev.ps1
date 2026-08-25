<#
.SYNOPSIS
    Loreweft AI Novel Engine - Frontend/Backend restart script
.DESCRIPTION
    Kills processes on port 8000 (backend) and 5173 (frontend), then restarts
    them as independent processes.
    Backend: py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
    Frontend: npm run dev
.PARAMETER BackendOnly
    Restart backend only
.PARAMETER FrontendOnly
    Restart frontend only
.PARAMETER NoLaunch
    Kill processes only, do not restart (cleanup mode)
.PARAMETER BackendWaitTimeoutSec
    Maximum time to wait for the backend health check. Large existing
    databases may need more than one minute for startup integrity checks.
.EXAMPLE
    .\restart-dev.ps1
    Restart both backend and frontend
.EXAMPLE
    .\restart-dev.ps1 -BackendOnly
    Restart backend only
#>
[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [switch]$NoLaunch,
    [ValidateRange(10, 600)]
    [int]$BackendWaitTimeoutSec = 180
)

$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$BackendPort = 8000
$FrontendPort = 5173

function Test-LoreweftProcessTree {
    param([int]$ProcessId, [string]$Label)
    $currentId = $ProcessId
    for ($depth = 0; $depth -lt 4 -and $currentId; $depth++) {
        $info = Get-CimInstance Win32_Process -Filter "ProcessId = $currentId"
        if (-not $info) { break }
        $command = [string]$info.CommandLine
        if ($Label -eq "backend" -and $command -match "uvicorn.+app\.main:app") { return $true }
        if ($Label -eq "frontend" -and $command -match "(?:vite|npm.+run.+dev)") { return $true }
        $currentId = [int]$info.ParentProcessId
    }
    return $false
}

function Stop-PortProcess {
    param([int]$Port, [string]$Label)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "  [$Label] port $Port not in use" -ForegroundColor Gray
        return
    }
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        $expected = Test-LoreweftProcessTree -ProcessId $procId -Label $Label
        if (-not $expected) {
            throw "[$Label] port $Port belongs to an unrelated process (PID=$procId). Refusing to terminate it."
        }
        # 1. kill child processes (uvicorn --reload forks multiprocessing workers
        #    that become orphans holding the port when only parent is killed)
        $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $procId }
        foreach ($child in $children) {
            $cp = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            if ($cp) {
                Write-Host "  [$Label] killing child PID=$($child.ProcessId) ($($cp.ProcessName))" -ForegroundColor DarkYellow
                Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        # 2. kill the parent (owning process)
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  [$Label] killing PID=$procId ($($proc.ProcessName))" -ForegroundColor Yellow
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        } else {
            # owning process not accessible (e.g. sandbox proxy) - force kill
            Write-Host "  [$Label] force killing PID=$procId (inaccessible)" -ForegroundColor Yellow
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 500
    # 3. verify port released. Never hunt and kill unrelated system-wide processes.
    $still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($still) {
        throw "[$Label] port $Port is still occupied after stopping the verified process."
    }
}

function Start-Backend {
    Write-Host "[backend] starting uvicorn on port $BackendPort..." -ForegroundColor Cyan
    $cmd = "cd '$BackendDir'; py -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort --reload"
    Start-Process powershell -ArgumentList "-NoProfile", "-Command", $cmd -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $ProjectRoot ".loreweft-backend.out.log") `
        -RedirectStandardError (Join-Path $ProjectRoot ".loreweft-backend.err.log") | Out-Null
}

function Start-Frontend {
    Write-Host "[frontend] starting vite on port $FrontendPort..." -ForegroundColor Cyan
    $cmd = "cd '$FrontendDir'; npm run dev"
    Start-Process powershell -ArgumentList "-NoProfile", "-Command", $cmd -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $ProjectRoot ".loreweft-frontend.out.log") `
        -RedirectStandardError (Join-Path $ProjectRoot ".loreweft-frontend.err.log") | Out-Null
}

function Wait-Port {
    param([int]$Port, [string]$Label, [int]$TimeoutSec = 20)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ($Label -eq "backend") {
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
                if ($health.status -eq "ok") {
                    Write-Host "  [$Label] health check OK" -ForegroundColor Green
                    return $true
                }
            } catch {}
        } else {
            $listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
            if ($listen) {
                Write-Host "  [$Label] port $Port listening OK" -ForegroundColor Green
                return $true
            }
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "  [$Label] port $Port wait timeout (>${TimeoutSec}s)" -ForegroundColor Red
    return $false
}

Write-Host "=== Loreweft Restart ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot" -ForegroundColor DarkGray
Write-Host ""

# Step 1: kill processes on ports
Write-Host "[1/2] Cleaning ports..." -ForegroundColor Yellow
$killBoth = -not $BackendOnly -and -not $FrontendOnly
if ($killBoth -or $BackendOnly) { Stop-PortProcess -Port $BackendPort -Label "backend" }
if ($killBoth -or $FrontendOnly) { Stop-PortProcess -Port $FrontendPort -Label "frontend" }
Write-Host ""

if ($NoLaunch) {
    Write-Host "[2/2] -NoLaunch mode: cleanup only, no restart" -ForegroundColor Magenta
    Write-Host "=== Done ===" -ForegroundColor Cyan
    exit 0
}

# Step 2: start services
Write-Host "[2/2] Starting services..." -ForegroundColor Yellow
if ($killBoth -or $BackendOnly) { Start-Backend }
if ($killBoth -or $FrontendOnly) { Start-Frontend }

# wait for ports to listen
Start-Sleep -Seconds 2
$results = @()
if ($killBoth -or $BackendOnly) { $results += Wait-Port -Port $BackendPort -Label "backend" -TimeoutSec $BackendWaitTimeoutSec }
if ($killBoth -or $FrontendOnly) { $results += Wait-Port -Port $FrontendPort -Label "frontend" -TimeoutSec 30 }

Write-Host ""
Write-Host "=== Restart Done ===" -ForegroundColor Cyan
if ($killBoth -or $BackendOnly) { Write-Host "  Backend API: http://127.0.0.1:$BackendPort" -ForegroundColor DarkGray }
if ($killBoth -or $FrontendOnly) { Write-Host "  Frontend UI: http://127.0.0.1:$FrontendPort" -ForegroundColor DarkGray }

if ($results -contains $false) {
    Write-Host "  WARNING: some service(s) timed out, check the PowerShell window logs" -ForegroundColor Red
    exit 1
}
