# Packaged AgentMax smoke test (maintainers) — simulates clean MSI/EXE launch.
# Requires: Python 3.11+ on PATH, release build at ui\src-tauri\target\release\AgentMax.exe

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "ui\src-tauri\target\release\AgentMax.exe"

if (-not (Test-Path $exe)) {
    Write-Error "Missing release binary. Run: cd ui; npm run tauri build"
}

foreach ($port in 7790, 7789) {
    Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}

Get-Process AgentMax -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

# Exercise the same defaults testers receive.
Remove-Item Env:AGENTMAX_BETA_SECURITY -ErrorAction SilentlyContinue
Remove-Item Env:AGENTMAX_SECURITY_MODE -ErrorAction SilentlyContinue
Remove-Item Env:AGENTMAX_IPC_AUTH -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -PassThru
Write-Host "Launched AgentMax PID=$($proc.Id)"

$healthOk = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
        Write-Error "AgentMax exited before backend came online (t=${i}s)"
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:7790/health" -TimeoutSec 2
        if ($health.ok) {
            $healthOk = $true
            Write-Host "Backend online at $($i + 1)s tester_id=$($health.tester_id)"
            break
        }
    } catch {}
}

if (-not $healthOk) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Write-Error "Backend :7790 never responded"
}

$second = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -PassThru
if (-not $second.WaitForExit(5000)) {
    Stop-Process -Id $second.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Write-Error "Second AgentMax instance did not exit; single-instance protection failed"
}
Write-Host "Single-instance protection: PASS"

Push-Location $root
python scripts\release_smoke_test.py
if ($LASTEXITCODE -ne 0) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Pop-Location
    exit $LASTEXITCODE
}
Pop-Location

$proc.Refresh()
if (-not $proc.HasExited) {
    $null = $proc.CloseMainWindow()
    if (-not $proc.WaitForExit(7000)) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}
$backend = Get-NetTCPConnection -LocalPort 7790 -State Listen -ErrorAction SilentlyContinue
if ($backend) {
    Stop-Process -Id $backend.OwningProcess -Force -ErrorAction SilentlyContinue
}
Write-Host "Packaged smoke test: PASS"
