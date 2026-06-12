@echo off
setlocal EnableDelayedExpansion
title AgentMax Master - Control Total
cd /d "%~dp0"

REM ════════════════════════════════════════════════════════════════════
REM   AgentMax MASTER (Beta v2.0)
REM   Tu launcher principal — coordinador, DDP, fine-tuning, todo.
REM ════════════════════════════════════════════════════════════════════
color 0A
mode con: cols=78 lines=32
echo.
echo  ╔══════════════════════════════════════════════════════════════════════════╗
echo  ║                                                                          ║
echo  ║    █████╗  ██████╗ ███████╗███╗   ██╗████████╗██████╗ ██╗██╗      ██████╗║
echo  ║   ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝██╔══██╗██║██║     ██╔═══██║
echo  ║   ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   ██████╔╝██║██║     ██║   █║
echo  ║   ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ██╔═══╝ ██║██║     ██║   █║
echo  ║   ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ██║     ██║███████╗╚██████║
echo  ║   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝     ╚═╝╚══════╝ ╚═════╝║
echo  ║                                                                          ║
echo  ║                          M A S T E R   v2.0                              ║
echo  ║                  Control total - Coordinador federado                    ║
echo  ╚══════════════════════════════════════════════════════════════════════════╝
echo.

REM ─── [1/5] Verificar Python ────────────────────────────────────────────────
echo  [1/5] Verificando Python...
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Python no encontrado en PATH.
    echo  Descarga Python 3.12 desde: https://www.python.org/downloads/
    echo  Marca "Add Python to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYTHON_VER=%%v
echo  [OK]  Python !PYTHON_VER!
echo.

REM ─── [2/5] Localizar/crear venv ────────────────────────────────────────────
echo  [2/5] Localizando entorno virtual...
set VENV_PATH=
if exist "..\..\AgentMax\.venv\Scripts\python.exe" set VENV_PATH=..\..\AgentMax\.venv
if exist "..\.venv\Scripts\python.exe"            set VENV_PATH=..\.venv
if exist ".venv\Scripts\python.exe"               set VENV_PATH=.venv

if "!VENV_PATH!"=="" (
    echo  [INFO] No se encontro venv — creando uno local...
    python -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] No se pudo crear el venv.
        pause
        exit /b 1
    )
    set VENV_PATH=.venv
    echo  [OK]  Venv creado en .venv
    set NEED_INSTALL=1
) else (
    echo  [OK]  Venv encontrado: !VENV_PATH!
)
set PYTHON_EXE=!VENV_PATH!\Scripts\python.exe
set TORCHRUN=!VENV_PATH!\Scripts\torchrun.exe
echo.

REM ─── [3/5] Instalar dependencias si es necesario ──────────────────────────
echo  [3/5] Verificando dependencias core...
"!PYTHON_EXE!" -c "import torch, datasets, trl" 2>nul
if errorlevel 1 set NEED_INSTALL=1

if defined NEED_INSTALL (
    echo  [INFO] Instalando dependencias minimas (torch, unsloth, trl, datasets)...
    echo         Esto puede tardar 5-15 min la primera vez.
    "!PYTHON_EXE!" -m pip install --upgrade pip --quiet
    "!PYTHON_EXE!" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121 --quiet
    "!PYTHON_EXE!" -m pip install unsloth trl datasets transformers peft accelerate bitsandbytes safetensors psutil cryptography --quiet
    if errorlevel 1 (
        echo  [WARN] Algunas dependencias fallaron. La UI puede funcionar igual.
    ) else (
        echo  [OK]  Dependencias instaladas.
    )
) else (
    echo  [OK]  Dependencias OK.
)
echo.

REM ─── [4/5] Verificar GPU y emitir info ────────────────────────────────────
echo  [4/5] Detectando GPU...
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>nul
if errorlevel 1 (
    echo  [WARN] nvidia-smi no encontrado. Si tienes GPU NVIDIA, instala los drivers.
    "!PYTHON_EXE!" -c "import torch; print(f'  torch CUDA: {torch.cuda.is_available()} - {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"sin GPU\"}')" 2>nul
)
echo.

REM ─── [5/5] Lanzar la UI Master ─────────────────────────────────────────────
echo  [5/5] Iniciando AgentMax Master UI...
echo.
echo  ┌──────────────────────────────────────────────────────────────────┐
echo  │  Tu IP local (ZeroTier o LAN):                                    │
ipconfig | findstr /C:"IPv4" | findstr /V "169.254"
echo  │                                                                   │
echo  │  Comparte AgentMax-Contributor.zip con tus amigos.              │
echo  │  Ellos solo necesitan poner tu IP y se conectan automaticamente. │
echo  └──────────────────────────────────────────────────────────────────┘
echo.

set MASTER_EXE=tauri-viz\src-tauri\target\release\tauri-viz.exe
if exist "!MASTER_EXE!" (
    echo  Abriendo interfaz grafica...
    start "AgentMax Master" "!MASTER_EXE!"
    timeout /t 2 >nul
    echo.
    echo  ✓ Master corriendo.
    echo.
    echo  Tabs disponibles:
    echo    • Coordinador  - Inicia el server federado para tus amigos
    echo    • Hardware     - Escaneo + benchmark + config optima
    echo    • Dispositivos - Tabla en vivo de quien esta conectado
    echo    • DDP          - Entrenar con 1 amigo via ZeroTier (clasico)
    echo    • Config       - Modelo base, output dir, bug collector
    echo.
    echo  Puedes cerrar esta ventana — la UI sigue corriendo.
) else (
    echo  [WARN] tauri-viz.exe no compilado.
    echo         Ejecuta: cd tauri-viz ^&^& npx tauri build
    echo.
    echo  Fallback: Iniciando coordinator en consola.
    "!PYTHON_EXE!" coordinator.py --port 12356 --min_contributors 1 --rounds 3
)

echo.
pause
endlocal
