# ===========================================================================
#   AgentMax - Build Release Script
#   Compila la UI Tauri y produce 2 paquetes ZIP:
#     1. AgentMax-Master.zip       (para ti)
#     2. AgentMax-Contributor.zip  (para tus amigos)
# ===========================================================================
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$NoZip,
    [string]$OutputDir = "release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host "   AgentMax - Build Release v2.0" -ForegroundColor Cyan
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host ""

# --- [1] Build Tauri UI -----------------------------------------------------
$masterExePath  = "tauri-viz\src-tauri\target\release\tauri-viz.exe"
$contribExePath = "tauri-viz\src-tauri\target\release\contributor.exe"

if ($SkipBuild) {
    Write-Host "[1/5] Skip build (parametro SkipBuild activado)" -ForegroundColor DarkGray
}
else {
    Write-Host "[1/5] Compilando UI Tauri (rebuild real)..." -ForegroundColor Yellow

    Push-Location "tauri-viz"
    $buildOk = $false
    try {
        # Verificar/instalar dependencias npm
        if (-not (Test-Path "node_modules\@tauri-apps\cli")) {
            Write-Host "      node_modules incompleto, ejecutando npm install..." -ForegroundColor DarkGray
            npm install --silent
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      [WARN] npm install fallo" -ForegroundColor Yellow
            }
        }

        # Build
        npx tauri build --no-bundle
        if ($LASTEXITCODE -eq 0) {
            $srcExe = "src-tauri\target\release\tauri-viz.exe"
            $dstExe = "src-tauri\target\release\contributor.exe"
            if (Test-Path $srcExe) {
                Copy-Item $srcExe $dstExe -Force
                Write-Host "      [OK] tauri-viz.exe y contributor.exe generados" -ForegroundColor Green
                $buildOk = $true
            }
        }
    } catch {
        Write-Host "      [WARN] Build excepcion: $($_.Exception.Message)" -ForegroundColor Yellow
    } finally {
        Pop-Location
    }

    if (-not $buildOk) {
        Write-Host ""
        Write-Host "      [INFO] No se pudo compilar la UI Tauri." -ForegroundColor Yellow
        Write-Host "             Continuo igual - los paquetes incluiran el modo consola Python." -ForegroundColor Yellow
        Write-Host "             Para arreglar mas tarde: cd tauri-viz && npm install && npx tauri build" -ForegroundColor DarkGray
        Write-Host ""
    }
}

# --- [2] Limpiar output dir -------------------------------------------------
Write-Host "[2/5] Preparando carpetas de salida..." -ForegroundColor Yellow
$masterDir      = Join-Path $OutputDir "AgentMax-Master"
$contributorDir = Join-Path $OutputDir "AgentMax-Contributor"

if (Test-Path $masterDir)      { Remove-Item $masterDir -Recurse -Force }
if (Test-Path $contributorDir) { Remove-Item $contributorDir -Recurse -Force }
New-Item $masterDir      -ItemType Directory -Force | Out-Null
New-Item $contributorDir -ItemType Directory -Force | Out-Null

$commonDirs = @("tauri-viz\src-tauri\target\release", "AgentMax-codetool-7b-v0.1\data")
foreach ($dir in $commonDirs) {
    New-Item (Join-Path $masterDir      $dir) -ItemType Directory -Force | Out-Null
    New-Item (Join-Path $contributorDir $dir) -ItemType Directory -Force | Out-Null
}

# --- [3] Master package -----------------------------------------------------
Write-Host "[3/5] Empaquetando MASTER..." -ForegroundColor Yellow

$masterPyFiles = @(
    "train_ddp.py", "coordinator.py", "contributor.py",
    "hardware_detective.py", "auto_config_engine.py",
    "crypto_db.py", "bug_collector.py",
    "generate_dataset.py",
    "optimize.ps1",
    "unsloth_train.py", "unsloth_train_max.py"
)
foreach ($f in $masterPyFiles) {
    if (Test-Path $f) { Copy-Item $f $masterDir }
}

if (Test-Path "AgentMax-codetool-7b-v0.1\data\train.jsonl") {
    Copy-Item "AgentMax-codetool-7b-v0.1\data\train.jsonl"  (Join-Path $masterDir "AgentMax-codetool-7b-v0.1\data\")
    Copy-Item "AgentMax-codetool-7b-v0.1\data\valid.jsonl"  (Join-Path $masterDir "AgentMax-codetool-7b-v0.1\data\")
}

# TODOS los launchers .bat y .ps1 del Master
$masterLaunchers = @(
    "AgentMax-Master.bat",
    "AgentMax-Contributor.bat",
    "Lanzar-Servidor.bat",
    "Lanzar-Servidor.ps1",
    "start_coordinator.bat",
    "start_contributor.bat",
    "start_contributor_smart.bat",
    "start_zerotier_all.bat",
    "rebuild_ui.bat",
    "build_release.bat",
    "COMO_USAR.md",
    "PLAN_MAESTRO.md",
    "README.md",
    "LEEME_DDP.md"
)
foreach ($f in $masterLaunchers) {
    if (Test-Path $f) {
        Copy-Item $f $masterDir
    }
}

# scripts/ folder con el build_release.ps1
$scriptsDir = Join-Path $masterDir "scripts"
New-Item $scriptsDir -ItemType Directory -Force | Out-Null
if (Test-Path "scripts\build_release.ps1") {
    Copy-Item "scripts\build_release.ps1" $scriptsDir
}

if (Test-Path $masterExePath) {
    Copy-Item $masterExePath (Join-Path $masterDir "tauri-viz\src-tauri\target\release\")
    Copy-Item $masterExePath (Join-Path $masterDir "AgentMax-Master.exe") -Force
    Write-Host "      [OK] tauri-viz.exe incluido" -ForegroundColor DarkGreen
} else {
    Write-Host "      [WARN] tauri-viz.exe no encontrado - paquete solo tendra modo consola" -ForegroundColor Yellow
}

$masterReadme = @'
# AgentMax Master (Beta v2.0)

## Como usar

1. Doble-click en AgentMax-Master.bat
2. La primera vez tarda 5-15 min (instala dependencias).
3. Se abre la UI. En la pestana Coordinador:
   - Configura el puerto (12356 por defecto)
   - Click en Iniciar
4. Tu IP aparece en la consola del launcher. Compartela con tus amigos.
5. Ellos ejecutan AgentMax-Contributor.bat y ponen tu IP.

## Tabs

- Coordinador: Server federado FedAvg (tus amigos se conectan aqui)
- Hardware: Escaneo + benchmark + config optima automatica
- Dispositivos: Tabla en vivo de quien esta conectado y entrenando
- DDP: Modo clasico con 1 amigo via ZeroTier
- Config: Modelo base, output dir, bug collector

## Requisitos

- Windows 10/11
- Python 3.10-3.12 (instalado y en PATH)
- GPU NVIDIA con CUDA 12.1+ (recomendado)
- ~30 GB de disco para modelo + dataset
'@

Set-Content -Path (Join-Path $masterDir "README.md") -Value $masterReadme -Encoding UTF8
Write-Host "      [OK] Master listo en $masterDir" -ForegroundColor Green

# --- [4] Contributor package ------------------------------------------------
Write-Host "[4/5] Empaquetando CONTRIBUTOR..." -ForegroundColor Yellow

$contribPyFiles = @(
    "train_ddp.py", "contributor.py",
    "hardware_detective.py", "auto_config_engine.py"
)
foreach ($f in $contribPyFiles) {
    if (Test-Path $f) { Copy-Item $f $contributorDir }
}

if (Test-Path "AgentMax-codetool-7b-v0.1\data\train.jsonl") {
    Copy-Item "AgentMax-codetool-7b-v0.1\data\train.jsonl"  (Join-Path $contributorDir "AgentMax-codetool-7b-v0.1\data\")
    Copy-Item "AgentMax-codetool-7b-v0.1\data\valid.jsonl"  (Join-Path $contributorDir "AgentMax-codetool-7b-v0.1\data\")
}

# Launchers que necesita el contributor
$contribLaunchers = @(
    "AgentMax-Contributor.bat",
    "start_contributor.bat",
    "start_contributor_smart.bat"
)
foreach ($f in $contribLaunchers) {
    if (Test-Path $f) {
        Copy-Item $f $contributorDir
    }
}

if (Test-Path $contribExePath) {
    Copy-Item $contribExePath (Join-Path $contributorDir "tauri-viz\src-tauri\target\release\")
    Copy-Item $contribExePath (Join-Path $contributorDir "AgentMax-Contributor.exe") -Force
    Write-Host "      [OK] contributor.exe incluido" -ForegroundColor DarkGreen
} elseif (Test-Path $masterExePath) {
    # Fallback: usar tauri-viz.exe renombrado como contributor.exe
    Copy-Item $masterExePath (Join-Path $contributorDir "tauri-viz\src-tauri\target\release\contributor.exe")
    Copy-Item $masterExePath (Join-Path $contributorDir "AgentMax-Contributor.exe") -Force
    Write-Host "      [OK] tauri-viz.exe copiado como contributor.exe" -ForegroundColor DarkGreen
} else {
    Write-Host "      [WARN] contributor.exe no encontrado - paquete solo tendra modo consola" -ForegroundColor Yellow
}

$contribReadme = @'
# AgentMax - Ayudar al fine-tuning

## Que es esto?

Tu amigo te paso este zip para ayudarlo a entrenar una IA llamada
AgentMax. Tu computadora prestara su GPU durante un rato para
acelerar el entrenamiento. No instala nada permanente en tu PC, no
manda datos personales, y puedes parar cuando quieras.

## Como usarlo (super facil)

1. Descomprime el zip donde quieras (Desktop sirve).
2. Doble-click en AgentMax-Contributor.bat
3. Windows te pedira permisos de admin - acepta (es para que tu GPU
   trabaje al 100%).
4. La primera vez tarda 5-15 min (descarga librerias). Despues es
   instantaneo.
5. Se abre una ventana - pega la IP que te paso tu amigo y click en
   Conectar GPU.
6. Listo! Tu GPU entrena. Puedes minimizar la ventana.

## Para parar

- Cierra la ventana o click en Desconectar.

## Requisitos

- Windows 10/11
- Python 3.10-3.12 instalado (descarga: https://python.org/downloads)
  IMPORTANTE: durante la instalacion marca "Add Python to PATH"
- GPU NVIDIA con CUDA 12.1+ (revisa con: nvidia-smi)
- Al menos 8 GB de VRAM recomendados

## Por que admin?

Para activar:
- Persistence mode de la GPU (menos latencia)
- Power limit maximo (mas TFLOPS)
- Prioridad REALTIME del proceso

Sin admin igual funciona, pero al 60-80% de rendimiento.

## Privacidad

- No se manda nada personal de tu PC.
- Solo se intercambian pesos numericos del modelo IA.
- Conexion TCP directa con tu amigo (no pasa por internet).
- Codigo abierto - todo lo que hace esta en estos archivos .py.
'@

Set-Content -Path (Join-Path $contributorDir "LEEME.txt") -Value $contribReadme -Encoding UTF8
Write-Host "      [OK] Contributor listo en $contributorDir" -ForegroundColor Green

# --- [5] Comprimir en ZIPs --------------------------------------------------
if (-not $NoZip) {
    Write-Host "[5/5] Comprimiendo ZIPs..." -ForegroundColor Yellow

    $masterZip = Join-Path $OutputDir "AgentMax-Master.zip"
    $contribZip = Join-Path $OutputDir "AgentMax-Contributor.zip"

    if (Test-Path $masterZip)  { Remove-Item $masterZip -Force }
    if (Test-Path $contribZip) { Remove-Item $contribZip -Force }

    Compress-Archive -Path $masterDir      -DestinationPath $masterZip  -CompressionLevel Optimal
    Compress-Archive -Path $contributorDir -DestinationPath $contribZip -CompressionLevel Optimal

    $masterSize  = "{0:N1}" -f ((Get-Item $masterZip).Length / 1MB)
    $contribSize = "{0:N1}" -f ((Get-Item $contribZip).Length / 1MB)

    Write-Host "      [OK] $masterZip  ($masterSize MB)"  -ForegroundColor Green
    Write-Host "      [OK] $contribZip ($contribSize MB)" -ForegroundColor Green
} else {
    Write-Host "[5/5] Skip zip (parametro NoZip activado)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host "  BUILD COMPLETO" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Tu:        Usa $masterDir\AgentMax-Master.bat"
Write-Host "  Amigos:    Compartiles $contribZip"
Write-Host ""
