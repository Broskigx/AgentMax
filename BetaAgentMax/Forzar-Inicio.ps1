# ===========================================================================
#   AgentMax - Todo en UN SOLO EXE
#   - Auto-elevacion admin
#   - Mata procesos previos
#   - Lanza el .exe que auto-inicia el servidor + GUI
#
#   USO:
#     powershell -ExecutionPolicy Bypass -File Forzar-Inicio.ps1
# ===========================================================================

param(
    [ValidateSet("Master","Contributor")]
    [string]$Modo = "Master"
)

$wp = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
$isAdmin = $wp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Solicitando permisos ADMIN..." -ForegroundColor Yellow
    $argsList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Modo", $Modo)
    Start-Process powershell -Verb RunAs -ArgumentList $argsList
    exit
}

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $scriptDir
$host.UI.RawUI.WindowTitle = "AgentMax - $Modo (TODO EN UNO)"

Clear-Host
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host "   AgentMax - $Modo (TODO EN UN EXE)" -ForegroundColor Cyan
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] Matando procesos previos..." -ForegroundColor Yellow
Get-Process -Name "tauri-viz","contributor" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*BetaAgentMax*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Write-Host "      [OK] Limpiado" -ForegroundColor Green
Write-Host ""

Write-Host "[2/3] Buscando ejecutable..." -ForegroundColor Yellow
if ($Modo -eq "Master") {
    $exePath = "$scriptDir\tauri-viz\src-tauri\target\release\tauri-viz.exe"
} else {
    $exePath = "$scriptDir\tauri-viz\src-tauri\target\release\contributor.exe"
}

if (-not (Test-Path $exePath)) {
    Write-Host "      [ERROR] No encontrado: $exePath" -ForegroundColor Red
    Read-Host "ENTER para salir"
    exit 1
}
Write-Host "      [OK] $exePath" -ForegroundColor Green
Write-Host ""

Write-Host "[3/3] Lanzando..." -ForegroundColor Green
Write-Host ""

$p = Start-Process -FilePath $exePath -WorkingDirectory $scriptDir -PassThru

if ($Modo -eq "Master") {
    Write-Host "      [OK] Servidor + GUI iniciados (PID: $($p.Id))" -ForegroundColor Green
    Write-Host "      El servidor arranca automaticamente en la GUI." -ForegroundColor DarkGray
    Write-Host "      Tus amigos se conectan con contributor.exe + tu IP." -ForegroundColor DarkGray
} else {
    Write-Host "      [OK] Contributor iniciado (PID: $($p.Id))" -ForegroundColor Green
    Write-Host "      Ingresa la IP del Master en la GUI y conecta." -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "      Ventana admin se cierra en 5 segundos..." -ForegroundColor DarkGray
Start-Sleep -Seconds 5
