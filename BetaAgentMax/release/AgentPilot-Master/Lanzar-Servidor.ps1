# ===========================================================================
#   NixControl - Lanzador Forzado del Servidor + Motor de Entrenamiento
#   - Auto-elevacion a admin
#   - Bypass de ExecutionPolicy
#   - Arranca ZeroTier
#   - Detecta IP virtual
#   - Abre firewall puerto 12356
#   - Lanza coordinator (servidor) + contributor local (tu GPU)
#
#   Ejecutar con doble-clic en Lanzar-Servidor.bat
#   O manual: powershell -ExecutionPolicy Bypass -File Lanzar-Servidor.ps1
# ===========================================================================

# --- Auto-elevacion a admin si no lo somos ---------------------------------
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Solicitando permisos de administrador..." -ForegroundColor Yellow
    $argsList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    Start-Process powershell -Verb RunAs -ArgumentList $argsList
    exit
}

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $scriptDir

Clear-Host
$host.UI.RawUI.WindowTitle = "NixControl - Servidor + Motor de Entrenamiento"

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host "   NixControl - Lanzador FORZADO" -ForegroundColor Cyan
Write-Host "   Servidor Federado + Motor de Entrenamiento (tu GPU)" -ForegroundColor Cyan
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host ""

# --- [1/6] Buscar ZeroTier -------------------------------------------------
Write-Host "[1/6] Buscando ZeroTier..." -ForegroundColor Yellow
$ztPaths = @(
    "C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat",
    "C:\ProgramData\ZeroTier\One\zerotier-cli.bat",
    "C:\Program Files\ZeroTier\One\zerotier-cli.bat",
    "C:\Program Files (x86)\ZeroTier\One\zerotier-one_x64.exe",
    "C:\ProgramData\ZeroTier\One\zerotier-one_x64.exe",
    "C:\Program Files\ZeroTier\One\zerotier-one_x64.exe"
)
$ztCli = $null
foreach ($p in $ztPaths) {
    if (Test-Path $p) {
        if ($p.EndsWith(".exe")) {
            $ztCli = @($p, "-q")
        } else {
            $ztCli = @($p)
        }
        break
    }
}

if (-not $ztCli) {
    Write-Host "      [WARN] ZeroTier no encontrado." -ForegroundColor Yellow
    Write-Host "      Sin ZT solo funciona en LAN local." -ForegroundColor Yellow
    Write-Host "      Descarga: https://www.zerotier.com/download/" -ForegroundColor DarkGray
    $useZT = $false
} else {
    Write-Host "      [OK] $($ztCli[0])" -ForegroundColor Green
    $useZT = $true
}
Write-Host ""

# --- [2/6] Arrancar servicio ZeroTier --------------------------------------
if ($useZT) {
    Write-Host "[2/6] Iniciando servicio ZeroTier..." -ForegroundColor Yellow
    $svc = Get-Service -Name "ZeroTierOneService" -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne "Running") {
        Start-Service -Name "ZeroTierOneService" -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    Write-Host "      [OK] Servicio activo" -ForegroundColor Green
    Write-Host ""

    # --- [3/6] Detectar IP virtual ZeroTier --------------------------------
    Write-Host "[3/6] Detectando IP virtual ZeroTier..." -ForegroundColor Yellow
    $netList = & $ztCli[0] $ztCli[1..($ztCli.Length-1)] listnetworks 2>$null
    $ztIp = $null
    foreach ($line in $netList) {
        if ($line -match "(\d+\.\d+\.\d+\.\d+)/\d+") {
            $ztIp = $matches[1]
            break
        }
    }

    if (-not $ztIp) {
        Write-Host "      [INFO] No estas unido a ninguna red ZeroTier." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "      Necesitas:" -ForegroundColor White
        Write-Host "        1. Cuenta gratis en https://my.zerotier.com" -ForegroundColor White
        Write-Host "        2. Crear una red (boton Create A Network)" -ForegroundColor White
        Write-Host "        3. Copiar el Network ID (16 caracteres)" -ForegroundColor White
        Write-Host ""
        $netId = Read-Host "      Network ID (o ENTER para saltar)"
        if ($netId) {
            & $ztCli[0] $ztCli[1..($ztCli.Length-1)] join $netId
            Write-Host ""
            Write-Host "      IMPORTANTE: En https://my.zerotier.com/network/$netId" -ForegroundColor Yellow
            Write-Host "                  marca el checkbox Auth de tu dispositivo." -ForegroundColor Yellow
            Write-Host "                  Esperando 15 segundos para que la IP se asigne..." -ForegroundColor DarkGray
            Start-Sleep -Seconds 15
            $netList = & $ztCli[0] $ztCli[1..($ztCli.Length-1)] listnetworks 2>$null
            foreach ($line in $netList) {
                if ($line -match "(\d+\.\d+\.\d+\.\d+)/\d+") {
                    $ztIp = $matches[1]
                    break
                }
            }
        }
    }

    if ($ztIp) {
        Write-Host "      [OK] Tu IP ZeroTier: $ztIp" -ForegroundColor Green
    } else {
        Write-Host "      [WARN] No se detecto IP ZeroTier - usaras IP local" -ForegroundColor Yellow
    }
    Write-Host ""
}

# --- [4/6] Abrir puerto en firewall ----------------------------------------
Write-Host "[4/6] Abriendo puerto 12356 en firewall..." -ForegroundColor Yellow
$ruleName = "NixControl Coordinator"
Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName $ruleName `
    -Direction Inbound -Protocol TCP -LocalPort 12356 `
    -Action Allow -Profile Any -ErrorAction SilentlyContinue | Out-Null
Write-Host "      [OK] Puerto 12356 abierto (TCP)" -ForegroundColor Green
Write-Host ""

# --- [5/6] Localizar venv y verificar dependencias -------------------------
Write-Host "[5/6] Localizando entorno Python..." -ForegroundColor Yellow
$venvCandidates = @(
    "..\.venv\Scripts\python.exe",
    ".venv\Scripts\python.exe",
    "C:\Users\agust\Desktop\Pruebas1\NixControl\.venv\Scripts\python.exe"
)
$pyExe = $null
foreach ($c in $venvCandidates) {
    if (Test-Path $c) { $pyExe = (Resolve-Path $c).Path; break }
}

if (-not $pyExe) {
    Write-Host "      [ERROR] Venv no encontrado." -ForegroundColor Red
    Write-Host "      Crea uno con: python -m venv .venv" -ForegroundColor DarkGray
    Read-Host "Presiona ENTER para salir"
    exit 1
}
Write-Host "      [OK] Python: $pyExe" -ForegroundColor Green

# Verificar imports
$importCheck = & $pyExe -c "import torch, unsloth, trl, datasets; print('ok')" 2>&1
if ($importCheck -notmatch "ok") {
    Write-Host "      [WARN] Faltan dependencias. Instalando..." -ForegroundColor Yellow
    & $pyExe -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cu121
    & $pyExe -m pip install --quiet unsloth trl datasets transformers peft accelerate bitsandbytes psutil
}
Write-Host ""

# --- [6/6] Mostrar info y lanzar -------------------------------------------
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host "   LISTO - COMPARTI ESTO CON TU AMIGO" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host ""
if ($ztIp) {
    Write-Host "   IP del coordinador:  $ztIp" -ForegroundColor White -BackgroundColor DarkGreen
    Write-Host "   Puerto:              12356" -ForegroundColor White -BackgroundColor DarkGreen
} else {
    Write-Host "   IP local (LAN):" -ForegroundColor White
    Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
        $_.IPAddress -notmatch "^(127|169\.254|192\.168\.56)\."
    } | ForEach-Object {
        Write-Host "     - $($_.IPAddress)" -ForegroundColor Green
    }
    Write-Host "   Puerto:              12356" -ForegroundColor White
}
Write-Host ""
Write-Host "   Tu amigo necesita:" -ForegroundColor White
Write-Host "     1. ZeroTier instalado + unido a la misma red" -ForegroundColor DarkGray
Write-Host "     2. Ejecutar start_contributor_smart.bat con esa IP" -ForegroundColor DarkGray
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host ""

# --- Lanzar coordinator (server federado) en esta ventana ------------------
Write-Host "Lanzando COORDINADOR..." -ForegroundColor Yellow
Write-Host "(esta ventana queda con los logs del servidor)" -ForegroundColor DarkGray
Write-Host ""

# Lanzar coordinator y dejarlo en foreground
Start-Process -FilePath $pyExe -ArgumentList @(
    "coordinator.py",
    "--port", "12356",
    "--min_contributors", "1",
    "--max_contributors", "100",
    "--rounds", "3",
    "--epochs_per_round", "1",
    "--train_dir", "`"$scriptDir`""
) -NoNewWindow -Wait

Write-Host ""
Write-Host "Coordinador finalizado." -ForegroundColor Yellow
Read-Host "Presiona ENTER para cerrar"
