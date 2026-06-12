@echo off
setlocal EnableDelayedExpansion
title NixControl Contributor - Auto-Setup
cd /d "%~dp0"

REM ============================================================
REM  Script inteligente: detecta Python correcto (3.10-3.12),
REM  crea venv, instala dependencias y conecta al coordinador.
REM  Funciona incluso si tienes Python 3.13 (te dice como arreglar).
REM ============================================================

color 0B
echo.
echo  ============================================================
echo    NixControl Contributor - Auto-Setup
echo  ============================================================
echo.

REM ─── Pedir admin para max power ──────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  Solicitando permisos de admin para max power GPU...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

REM ─── Detectar Python compatible ──────────────────────────────
echo  [1/4] Buscando Python compatible (3.10, 3.11 o 3.12)...
set PY_CMD=
set PY_VER=

REM Intentar py launcher (mejor)
where py >nul 2>&1
if not errorlevel 1 (
    for %%v in (3.12 3.11 3.10) do (
        py -%%v --version >nul 2>&1
        if not errorlevel 1 (
            set PY_CMD=py -%%v
            set PY_VER=%%v
            goto :PY_FOUND
        )
    )
)

REM Fallback: python en PATH
where python >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
    echo  [INFO] Python !PY_VER! detectado en PATH

    REM Chequear si es 3.10-3.12
    echo !PY_VER! | findstr /R "^3\.1[012]\." >nul
    if not errorlevel 1 (
        set PY_CMD=python
        goto :PY_FOUND
    )
)

REM ─── No hay Python compatible ────────────────────────────────
color 0E
echo.
echo  [ERROR] No se encontro Python 3.10, 3.11 ni 3.12.
echo.
echo  Detectado: !PY_VER! (no compatible con Unsloth todavia)
echo.
echo  SOLUCION:
echo  ---------
echo  1. Descarga Python 3.12 desde:
echo     https://www.python.org/downloads/release/python-31210/
echo.
echo  2. Durante la instalacion marca:
echo     [X] Add Python 3.12 to PATH
echo     [X] py launcher
echo.
echo  3. Despues volve a ejecutar este archivo.
echo.
echo  No desinstales Python !PY_VER!, ambos pueden coexistir.
echo.
start "" "https://www.python.org/downloads/release/python-31210/"
pause
exit /b 1

:PY_FOUND
echo  [OK] Python %PY_VER% via "%PY_CMD%"
echo.

REM ─── Crear/verificar venv ─────────────────────────────────────
echo  [2/4] Configurando entorno virtual (.venv)...
if not exist ".venv\Scripts\python.exe" (
    echo  Creando venv con Python %PY_VER%...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] No se pudo crear el venv.
        pause
        exit /b 1
    )
    set NEED_INSTALL=1
)
set VENV_PY=.venv\Scripts\python.exe
echo  [OK] Venv en .venv
echo.

REM ─── Instalar dependencias ────────────────────────────────────
echo  [3/4] Verificando dependencias...
"%VENV_PY%" -c "import torch, unsloth, trl" 2>nul
if errorlevel 1 set NEED_INSTALL=1

if defined NEED_INSTALL (
    echo  Instalando librerias (puede tardar 5-15 min)...
    echo.
    "%VENV_PY%" -m pip install --upgrade pip --quiet
    echo  [1/3] Instalando torch + CUDA 12.1...
    "%VENV_PY%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121 --quiet --extra-index-url https://pypi.org/simple/
    if errorlevel 1 (
        echo  [WARN] torch con cu121 fallo, intentando cu124...
        "%VENV_PY%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124 --quiet --extra-index-url https://pypi.org/simple/
    )
    echo  [2/3] Instalando unsloth...
    "%VENV_PY%" -m pip install unsloth --quiet
    echo  [3/3] Instalando resto (trl, datasets, etc)...
    "%VENV_PY%" -m pip install trl datasets transformers peft accelerate bitsandbytes safetensors psutil --quiet
    if errorlevel 1 (
        color 0C
        echo  [ERROR] Falla en instalacion. Revisa tu conexion a internet.
        pause
        exit /b 1
    )
    echo  [OK] Todo instalado.
) else (
    echo  [OK] Dependencias ya instaladas.
)
echo.

REM ─── Pedir IP y conectar ──────────────────────────────────────
echo  [4/4] Listo para conectar.
echo.
echo  ============================================================
echo.
set /p COORD_IP="  IP del coordinador (la que te paso tu amigo): "
if "%COORD_IP%"=="" (
    echo  [ERROR] IP requerida.
    pause
    exit /b 1
)
set /p COORD_PORT="  Puerto (Enter = 12356): "
if "%COORD_PORT%"=="" set COORD_PORT=12356

echo.
echo  Conectando a %COORD_IP%:%COORD_PORT% con MAX POWER...
echo  (cierra esta ventana o Ctrl+C para parar)
echo.

"%VENV_PY%" contributor.py ^
    --coordinator_ip %COORD_IP% ^
    --coordinator_port %COORD_PORT% ^
    --train_dir "%CD%" ^
    --torchrun ".venv\Scripts\torchrun.exe" ^
    --max_power ^
    --power_target 90

echo.
echo  Contribucion finalizada.
pause
endlocal
