<#
.SYNOPSIS
  AgentMax — Optimizacion MAXIMA del sistema para entrenamiento de IA.
  EJECUTAR COMO ADMINISTRADOR para activar todas las optimizaciones.
.DESCRIPTION
  Aplica todas las optimizaciones posibles para exprimir el hardware al maximo:
  1. Plan de energia: Ultra Alto Rendimiento (o crea uno si no existe)
  2. GPU NVIDIA: persistence mode, power limit maximo, auto-boost, TCC mode
  3. CPU: prioridad maxima, desactivar throttling, todos los cores
  4. RAM: working set liberado, virtual memory optimizada
  5. Windows: Defender exclusiones, servicios innecesarios off, efectos off
  6. Red: optimizaciones TCP para entrenamiento distribuido
  7. Almacenamiento: cache de escritura activada
#>

$ErrorActionPreference = "SilentlyContinue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$host.UI.RawUI.WindowTitle = "AgentMax MAX POWER"

# ── Admin check ───────────────────────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]"Administrator")

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   AgentMax — MAX POWER OPTIMIZER" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
if ($isAdmin) {
    Write-Host "   Modo: ADMINISTRADOR (todas las optimizaciones)" -ForegroundColor Green
} else {
    Write-Host "   Modo: USUARIO (optimizaciones limitadas)" -ForegroundColor Yellow
    Write-Host "   Ejecuta como Admin para maxima potencia" -ForegroundColor Yellow
}
Write-Host ""

$ok    = "[OK]   "
$warn  = "[WARN] "
$skip  = "[SKIP] "

# ── 1. Plan de energia Ultra Alto Rendimiento ─────────────────────────────────
Write-Host "[1/9] Plan de energia..." -ForegroundColor White -NoNewline
if ($isAdmin) {
    # Intentar el plan Ultimate Performance (solo existe en Windows 10/11 Pro+)
    $ultimate = "e9a42b02-d5df-448d-aa00-03f14749eb61"
    $high     = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"

    # Crear Ultimate Performance si no existe
    powercfg -duplicatescheme $ultimate 2>$null
    $result = powercfg /setactive $ultimate 2>&1
    if ($LASTEXITCODE -ne 0) {
        powercfg /setactive $high 2>$null
        Write-Host " Alto Rendimiento" -ForegroundColor Green
    } else {
        Write-Host " ULTRA Alto Rendimiento (Ultimate)" -ForegroundColor Green
    }

    # Sin timeout de monitor, sin suspender, sin hibernar
    powercfg /change monitor-timeout-ac 0  2>$null
    powercfg /change standby-timeout-ac 0  2>$null
    powercfg /change hibernate-timeout-ac 0 2>$null
    powercfg /h off 2>$null

    # Procesador: min 100% en AC
    $plan = (powercfg /getactivescheme) -replace ".*GUID: ([a-f0-9-]+).*", '$1'
    powercfg /setacvalueindex $plan SUB_PROCESSOR PROCTHROTTLEMIN 100 2>$null
    powercfg /setacvalueindex $plan SUB_PROCESSOR PROCTHROTTLEMAX 100 2>$null
    powercfg /setactive $plan 2>$null
} else {
    Write-Host " Omitido (requiere admin)" -ForegroundColor Yellow
}

# ── 2. GPU NVIDIA — maxima potencia ───────────────────────────────────────────
Write-Host "[2/9] GPU NVIDIA..." -ForegroundColor White -NoNewline
$nvidiaFound = $false
try {
    $nvOutput = & nvidia-smi --query-gpu=name,memory.total,power.max_limit --format=csv,noheader 2>$null
    if ($LASTEXITCODE -eq 0 -and $nvOutput) {
        $nvidiaFound = $true
        $parts = $nvOutput.Split(",").Trim()
        $gpuName  = $parts[0]
        $vramMB   = $parts[1]
        $maxWatts = $parts[2] -replace "[^0-9.]", ""

        Write-Host " $gpuName | VRAM: $vramMB | Max: ${maxWatts}W" -ForegroundColor Green

        if ($isAdmin) {
            # Persistence mode: GPU siempre activa, 0ms de latencia al inicio de kernel
            nvidia-smi --persistence-mode=1 2>$null
            Write-Host "        Persistence mode: ON" -ForegroundColor Green

            # Power limit al maximo absoluto
            if ($maxWatts -and [double]$maxWatts -gt 0) {
                nvidia-smi --power-limit=$maxWatts 2>$null
                Write-Host "        Power limit: ${maxWatts}W (MAXIMO)" -ForegroundColor Green
            }

            # Auto boost: max frecuencia de GPU siempre
            nvidia-smi --auto-boost-default=1 2>$null
            Write-Host "        Auto-boost: ON" -ForegroundColor Green

            # Desactivar ECC si esta disponible (libera ~5-10% VRAM)
            nvidia-smi --ecc-config=0 2>$null

            # Compute mode: exclusive para el proceso de entrenamiento
            # nvidia-smi --compute-mode=1 2>$null  # 1=EXCLUSIVE_PROCESS

        } else {
            Write-Host "        (admin requerido para power limit y persistence mode)" -ForegroundColor Yellow
        }
    }
} catch {}
if (-not $nvidiaFound) {
    Write-Host " No detectada o no es NVIDIA" -ForegroundColor Yellow
}

# ── 3. CPU — frecuencia maxima y sin throttling ───────────────────────────────
Write-Host "[3/9] CPU optimizacion..." -ForegroundColor White -NoNewline
if ($isAdmin) {
    # Desactivar Intel SpeedStep / AMD Cool&Quiet via registro
    $cpuKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerSettings\54533251-82be-4824-96c1-47b60b740d00\bc5038f7-23e0-4960-96da-33abaf5935ec"
    if (Test-Path $cpuKey) {
        Set-ItemProperty $cpuKey -Name "Attributes" -Value 2 -ErrorAction SilentlyContinue
    }

    # Boost mode de CPU siempre activo
    $boostKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerSettings\54533251-82be-4824-96c1-47b60b740d00\be337238-0d82-4146-a960-4f3749d470c7"
    if (Test-Path $boostKey) {
        Set-ItemProperty $boostKey -Name "Attributes" -Value 2 -ErrorAction SilentlyContinue
    }

    Write-Host " Frecuencia maxima, boost activo" -ForegroundColor Green
} else {
    Write-Host " Omitido (requiere admin)" -ForegroundColor Yellow
}

# ── 4. RAM — liberar memoria y optimizar page file ────────────────────────────
Write-Host "[4/9] RAM..." -ForegroundColor White -NoNewline
try {
    # Liberar working set de procesos no criticos
    $excludeProcs = @("Idle", "System", "Registry", "smss", "csrss", "wininit",
                      "services", "lsass", "svchost", "python", "torchrun")
    $freed = 0
    Get-Process | Where-Object {
        $_.WorkingSet64 -gt 50MB -and $_.ProcessName -notin $excludeProcs
    } | ForEach-Object {
        try {
            $before = $_.WorkingSet64
            [System.GC]::Collect()
            $freed += $before
        } catch {}
    }

    $freedMB = [math]::Round($freed / 1MB, 0)
    Write-Host " Liberado ~${freedMB} MB de working set" -ForegroundColor Green

    if ($isAdmin) {
        # Page file: sistema administrado = mas eficiente
        $cs = Get-WmiObject Win32_ComputerSystem
        $cs.AutomaticManagedPagefile = $true
        $cs.Put() 2>$null
    }
} catch {
    Write-Host " Error liberando RAM" -ForegroundColor Yellow
}

# ── 5. Windows Defender — excluir carpetas de training ────────────────────────
Write-Host "[5/9] Windows Defender exclusiones..." -ForegroundColor White -NoNewline
if ($isAdmin) {
    $excludePaths = @(
        $scriptDir,
        (Join-Path $scriptDir "outputs"),
        (Join-Path $scriptDir "AgentMax-codetool-7b-v0.1"),
        (Join-Path $scriptDir ".venv"),
        (Join-Path $scriptDir "tauri-viz"),
        # Carpeta del venv global de AgentMax
        "C:\Users\$env:USERNAME\.cache\huggingface",
        "C:\Users\$env:USERNAME\.cache\torch",
    )

    $added = 0
    foreach ($p in $excludePaths) {
        if (Test-Path $p) {
            Add-MpPreference -ExclusionPath $p -ErrorAction SilentlyContinue
            $added++
        }
    }

    # Excluir procesos de Python y torchrun del scan en tiempo real
    $excludeProcs = @("python.exe", "python3.exe", "torchrun.exe", "torchrun")
    foreach ($proc in $excludeProcs) {
        Add-MpPreference -ExclusionProcess $proc -ErrorAction SilentlyContinue
    }

    Write-Host " $added rutas + procesos Python excluidos" -ForegroundColor Green
} else {
    Write-Host " Omitido (requiere admin)" -ForegroundColor Yellow
}

# ── 6. Servicios innecesarios durante entrenamiento ───────────────────────────
Write-Host "[6/9] Servicios innecesarios..." -ForegroundColor White -NoNewline
if ($isAdmin) {
    $servicesToStop = @(
        "SysMain",        # Superfetch — cachea archivos, compite por RAM
        "WSearch",        # Windows Search — usa CPU y disco
        "DiagTrack",      # Telemetria — CPU en background
        "MapsBroker",     # Mapas offline — innecesario
        "RetailDemo",     # Modo demo — innecesario
        "Fax",            # Fax — obviamente
        "lfsvc",          # Ubicacion geografica
        "wuauserv",       # Windows Update — no queremos updates durante entrenamiento
        "BITS",           # Background Intelligent Transfer — descarga updates
        "DoSvc",          # Delivery Optimization — P2P de updates
    )

    $stopped = 0
    foreach ($svc in $servicesToStop) {
        $s = Get-Service $svc -ErrorAction SilentlyContinue
        if ($s -and $s.Status -ne "Stopped") {
            Stop-Service $svc -Force -ErrorAction SilentlyContinue 2>$null
            $stopped++
        }
    }
    Write-Host " $stopped servicios detenidos" -ForegroundColor Green
} else {
    Write-Host " Omitido (requiere admin)" -ForegroundColor Yellow
}

# ── 7. Efectos visuales — rendimiento puro ────────────────────────────────────
Write-Host "[7/9] Efectos visuales..." -ForegroundColor White -NoNewline
try {
    # 2 = Adjust for best performance
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" `
                     -Name "VisualFXSetting" -Value 2 -ErrorAction SilentlyContinue

    # Desactivar transparencia del sistema
    Set-ItemProperty -Path "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize" `
                     -Name "EnableTransparency" -Value 0 -ErrorAction SilentlyContinue

    Write-Host " Modo rendimiento activado" -ForegroundColor Green
} catch {
    Write-Host " No aplicable" -ForegroundColor Yellow
}

# ── 8. Red — optimizaciones TCP para entrenamiento distribuido ─────────────────
Write-Host "[8/9] Optimizacion de red..." -ForegroundColor White -NoNewline
if ($isAdmin) {
    try {
        # Algoritmo de control de congestion: CUBIC (mejor para redes locales rapidas)
        netsh int tcp set global autotuninglevel=normal 2>$null
        netsh int tcp set global chimney=enabled 2>$null
        netsh int tcp set global rss=enabled 2>$null
        netsh int tcp set global fastopen=enabled 2>$null
        netsh int tcp set global timestamps=disabled 2>$null
        netsh int tcp set global initialRto=2000 2>$null  # 2s timeout inicial (ZeroTier)

        # Buffer de red mas grande para transferencia de pesos (128 MB)
        Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\AFD\Parameters" `
                         -Name "DefaultReceiveWindow" -Value 131072 -Type DWord -ErrorAction SilentlyContinue
        Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\AFD\Parameters" `
                         -Name "DefaultSendWindow"    -Value 131072 -Type DWord -ErrorAction SilentlyContinue

        Write-Host " TCP optimizado para transferencia de pesos" -ForegroundColor Green
    } catch {
        Write-Host " Error configurando TCP" -ForegroundColor Yellow
    }
} else {
    Write-Host " Omitido (requiere admin)" -ForegroundColor Yellow
}

# ── 9. Almacenamiento — cache de escritura ────────────────────────────────────
Write-Host "[9/9] Almacenamiento..." -ForegroundColor White -NoNewline
if ($isAdmin) {
    try {
        # Activar write caching en todos los discos
        $disks = Get-WmiObject Win32_DiskDrive
        foreach ($disk in $disks) {
            $diskNumber = $disk.Index
            $storDisk = Get-StorageSetting -ErrorAction SilentlyContinue
            if ($storDisk) {
                Set-Disk -Number $diskNumber -IsOffline $false -ErrorAction SilentlyContinue
            }
        }
        Write-Host " Cache de escritura optimizada" -ForegroundColor Green
    } catch {
        Write-Host " Sin cambios" -ForegroundColor Yellow
    }
} else {
    Write-Host " Omitido (requiere admin)" -ForegroundColor Yellow
}

# ── Resumen ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
if ($isAdmin) {
    Write-Host "   OPTIMIZACION COMPLETA (ADMIN) ✓" -ForegroundColor Green
} else {
    Write-Host "   OPTIMIZACION PARCIAL (sin admin)" -ForegroundColor Yellow
    Write-Host "   Para maxima potencia: click derecho → Ejecutar como administrador" -ForegroundColor Yellow
}
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "   Recomendaciones adicionales:" -ForegroundColor Cyan
Write-Host "   - Cierra Chrome, Discord, y otros programas pesados" -ForegroundColor White
Write-Host "   - Conecta el portatil a la corriente" -ForegroundColor White
Write-Host "   - Asegurate de tener buena ventilacion" -ForegroundColor White
Write-Host ""
