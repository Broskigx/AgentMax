@echo off
title NixControl Coordinator (Master)
cd /d "%~dp0"

REM ============================================================
REM  Arranca el Coordinador federado directamente con Python.
REM  No depende del .exe Tauri (que tiene un bug en el build viejo).
REM ============================================================

set VENV=..\.venv
if not exist "%VENV%\Scripts\python.exe" (
    if exist "C:\Users\agust\Desktop\Pruebas1\NixControl\.venv\Scripts\python.exe" (
        set VENV=C:\Users\agust\Desktop\Pruebas1\NixControl\.venv
    ) else (
        echo [ERROR] Venv no encontrado en %VENV%
        echo Buscado en:
        echo   %CD%\..\.venv
        echo   C:\Users\agust\Desktop\Pruebas1\NixControl\.venv
        pause
        exit /b 1
    )
)

REM ─── Config ─────────────────────────────────────────────────
set PORT=12356
set MIN_CONTRIBUTORS=1
set MAX_CONTRIBUTORS=100
set ROUNDS=3
set EPOCHS_PER_ROUND=1
REM ────────────────────────────────────────────────────────────

echo ============================================
echo   NixControl Coordinator Federado
echo ============================================
echo   Python: %VENV%\Scripts\python.exe
echo   Puerto: %PORT%
echo   Rondas: %ROUNDS%  Epochs/ronda: %EPOCHS_PER_ROUND%
echo   Min/Max contributors: %MIN_CONTRIBUTORS% / %MAX_CONTRIBUTORS%
echo.
echo   Tu IP local:
ipconfig | findstr /C:"IPv4" | findstr /V "169.254"
echo.
echo   Comparte tu IP con tus amigos. Ellos usan start_contributor.bat.
echo ============================================
echo.

"%VENV%\Scripts\python.exe" coordinator.py ^
    --port %PORT% ^
    --min_contributors %MIN_CONTRIBUTORS% ^
    --max_contributors %MAX_CONTRIBUTORS% ^
    --rounds %ROUNDS% ^
    --epochs_per_round %EPOCHS_PER_ROUND% ^
    --train_dir "%CD%"

echo.
echo Coordinador finalizado.
pause
