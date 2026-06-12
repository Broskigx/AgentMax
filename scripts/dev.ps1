<#
.SYNOPSIS
    Start AgentMax in development mode.
    Launches the Python beta backend + Tauri UI.
#>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $env:VIRTUAL_ENV) {
    if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
        Write-Error "Missing .venv. Run scripts\setup.ps1 first."
    }
    & ".venv\Scripts\Activate.ps1"
}

Write-Host "`n  Starting AgentMax Dev..." -ForegroundColor Cyan

$pyJob = Start-Job -ScriptBlock {
    Set-Location $using:Root
    & ".venv\Scripts\python.exe" "scripts\agentmax_server.py"
} -Name "AgentMax-Backend"

Write-Host "  Python backend (auto: runtime -> beta fallback) on :7790 (Job ID: $($pyJob.Id))..." -ForegroundColor Green
Start-Sleep -Seconds 2

$env:AGENTMAX_SKIP_BACKEND_SPAWN = "1"
Write-Host "  Starting Tauri UI (backend already running)..." -ForegroundColor Green
Push-Location "ui"
try {
    npm run tauri dev
} finally {
    Pop-Location
    Remove-Item Env:\AGENTMAX_SKIP_BACKEND_SPAWN -ErrorAction SilentlyContinue
}

Write-Host "`n  Stopping Python backend..." -ForegroundColor Yellow
Stop-Job -Job $pyJob -ErrorAction SilentlyContinue
Remove-Job -Job $pyJob -ErrorAction SilentlyContinue