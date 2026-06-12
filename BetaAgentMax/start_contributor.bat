@echo off
title AgentMax Contributor
cd /d "%~dp0"

REM ============================================================
REM  Arranca el Contributor directamente con Python.
REM  Solicita admin para max power.
REM ============================================================

REM ─── Pedir admin si no lo somos ─────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando permisos de admin para max power...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

set VENV=..\.venv
if not exist "%VENV%\Scripts\python.exe" (
    if exist "C:\Users\agust\Desktop\Pruebas1\AgentMax\.venv\Scripts\python.exe" (
        set VENV=C:\Users\agust\Desktop\Pruebas1\AgentMax\.venv
    ) else (
        echo [ERROR] Venv no encontrado.
        pause
        exit /b 1
    )
)

echo ============================================
echo   AgentMax Contributor
echo ============================================
echo   Python: %VENV%\Scripts\python.exe
echo.

REM ─── Pedir IP del coordinador ───────────────────────────────
set /p COORD_IP="IP del coordinador (ej: 192.168.196.1): "
if "%COORD_IP%"=="" (
    echo [ERROR] IP requerida.
    pause
    exit /b 1
)
set /p COORD_PORT="Puerto (Enter = 12356): "
if "%COORD_PORT%"=="" set COORD_PORT=12356

echo.
echo Conectando a %COORD_IP%:%COORD_PORT% con objetivo real 90%%...
echo.

"%VENV%\Scripts\python.exe" contributor.py ^
    --coordinator_ip %COORD_IP% ^
    --coordinator_port %COORD_PORT% ^
    --train_dir "%CD%" ^
    --torchrun "%VENV%\Scripts\torchrun.exe" ^
    --max_power ^
    --power_target 90

echo.
pause
