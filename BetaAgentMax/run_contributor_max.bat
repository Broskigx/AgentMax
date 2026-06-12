@echo off
title AgentMax - CONTRIBUIR GPU (MAX POWER)
cd /d "%~dp0"

:: ─── Verificar si somos ADMIN ──────────────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ============================================
    echo   AgentMax - CONTRIBUIR GPU MAX POWER
    echo ============================================
    echo.
    echo  Solicitando permisos de ADMINISTRADOR...
    echo  (Necesario para GPU persistence mode,
    echo   power limit maximo y prioridad REALTIME)
    echo.
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: ─── Somos ADMIN ─────────────────────────────────────────────────────────
echo ============================================
echo   AgentMax - CONTRIBUIR GPU MAX POWER
echo   Permisos: ADMINISTRADOR [OK]
echo ============================================
echo.

:: Variables de entorno para maxima performance CUDA
set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512,roundup_power2_divisions:8
set CUDA_LAUNCH_BLOCKING=0
set OMP_NUM_THREADS=%NUMBER_OF_PROCESSORS%
set MKL_NUM_THREADS=%NUMBER_OF_PROCESSORS%
set TOKENIZERS_PARALLELISM=false
set TRANSFORMERS_NO_ADVISORY_WARNINGS=1
set CUDA_DEVICE_MAX_CONNECTIONS=8
set NCCL_IB_DISABLE=1
set NCCL_P2P_DISABLE=1
set NCCL_SOCKET_IFNAME=^*

:: Deshabilitar Windows Error Reporting para no pausar el proceso
reg add "HKCU\Software\Microsoft\Windows\Windows Error Reporting" /v "DontShowUI" /t REG_DWORD /d 1 /f >nul 2>&1

:: ─── [1/4] Optimizar sistema ──────────────────────────────────────────────
echo [1/4] Optimizando sistema para maximo rendimiento...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0optimize.ps1"
echo.

:: ─── [2/4] Detectar GPU ───────────────────────────────────────────────────
echo [2/4] Detectando GPU...
nvidia-smi --query-gpu=name,memory.total,driver_version,power.limit --format=csv,noheader 2>nul
if errorlevel 1 (
    echo [WARN] nvidia-smi no encontrado. GPU no detectada via NVIDIA.
    python -c "import torch; print(f'  torch GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"No disponible\"}')" 2>nul
)
echo.

:: ─── [3/4] Info del sistema ───────────────────────────────────────────────
echo [3/4] Recursos disponibles:
python -c "import torch, os; print(f'  CPU cores: {os.cpu_count()}'); print(f'  GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}'); print(f'  VRAM: {torch.cuda.get_device_properties(0).total_memory/1e9:.1f} GB' if torch.cuda.is_available() else '  VRAM: N/A')" 2>nul
echo.

:: ─── [4/4] Iniciar contribucion ───────────────────────────────────────────
echo [4/4] Iniciando contribucion MAX POWER...
echo.
echo ============================================
echo   INSTRUCCIONES
echo ============================================
echo   Esta ventana iniciara la UI de contribucion.
echo   En la UI:
echo     1. Ingresa la IP del coordinador
echo     2. Haz click en "CONECTAR y CONTRIBUIR"
echo     3. Tu GPU entrenara a potencia MAXIMA
echo.
echo   Alternativa (consola sin UI):
echo     python contributor.py --coordinator_ip IP --max_power
echo ============================================
echo.

:: Buscar contributor.exe compilado
if exist "%~dp0tauri-viz\src-tauri\target\release\contributor.exe" (
    echo Iniciando interfaz grafica...
    :: Ejecutar con prioridad HIGH para que la UI responda bien
    start "AgentMax Contributor" /HIGH "%~dp0tauri-viz\src-tauri\target\release\contributor.exe"
    echo Interfaz abierta. Puedes cerrar esta ventana.
    timeout /t 3 >nul
    exit /b 0
)

:: Fallback: modo consola interactivo
echo [INFO] UI no encontrada. Modo consola.
echo.
set /p COORD_IP="IP del coordinador (ej: 192.168.196.1): "
set /p COORD_PORT="Puerto (Enter=12356): "
if "%COORD_PORT%"=="" set COORD_PORT=12356

echo.
echo Conectando a %COORD_IP%:%COORD_PORT% con MAX POWER...
echo.

:: Buscar Python del venv
set PYTHON=python
if exist "..\.venv\Scripts\python.exe" set PYTHON=..\.venv\Scripts\python.exe
if exist ".venv\Scripts\python.exe"   set PYTHON=.venv\Scripts\python.exe

:: Lanzar con prioridad HIGH y objetivo real 90%% para no bloquear Windows
start "AgentMax Training" /HIGH /B %PYTHON% contributor.py ^
    --coordinator_ip %COORD_IP% ^
    --coordinator_port %COORD_PORT% ^
    --train_dir "%CD%" ^
    --max_power ^
    --power_target 90

echo.
echo Contribucion iniciada con objetivo real 90%%.
echo Presiona Ctrl+C para detener.
pause
