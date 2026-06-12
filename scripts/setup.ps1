<#
.SYNOPSIS
    AgentMax closed-beta setup for Windows.
.DESCRIPTION
    Requires Python 3.11+, Node 20+, npm and Rust/Cargo for Tauri dev builds.
#>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Require($cmd, $name) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Error "Required tool not found: $name. Install it before continuing."
    }
}

Require "python" "Python 3.11+"
Require "node" "Node.js 20+"
Require "npm" "npm"
Require "cargo" "Rust (for Tauri)"

$pyVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "Using Python $pyVersion" -ForegroundColor Cyan

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& ".venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip
if (-not (Test-Path "requirements.txt")) {
    Write-Error "Missing requirements.txt in $Root"
}
pip install -r requirements.txt

New-Item -ItemType Directory -Force -Path "data" | Out-Null
New-Item -ItemType Directory -Force -Path "logs\agentmax" | Out-Null
New-Item -ItemType Directory -Force -Path "diagnostics" | Out-Null

Push-Location "ui"
npm install
npm run build
Push-Location "src-tauri"
cargo check
Pop-Location
Pop-Location

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example" -ForegroundColor Yellow
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "  Backend:  python scripts\agentmax_server.py" -ForegroundColor Cyan
Write-Host "  Dev app:  .\scripts\dev.ps1" -ForegroundColor Cyan
Write-Host "  Smoke:    python scripts\release_smoke_test.py" -ForegroundColor Cyan