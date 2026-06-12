@echo off
title AgentMax - Setup
cd /d "%~dp0"
echo ============================================
echo   AgentMax - Setup Completo
echo ============================================
echo.
echo [1/4] Verificando Python...
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no encontrado. Instala Python 3.12
    pause
    exit /b 1
)
echo [OK] Python encontrado

echo.
echo [2/4] Verificando Rust + Node...
where rustc >nul 2>&1 || echo [WARN] rustc no encontrado (solo para UI)
where node >nul 2>&1 || echo [WARN] node no encontrado (solo para UI)
echo [OK]

echo.
echo [3/4] Instalando dependencias npm...
cd tauri-viz
if exist node_modules (
    echo npm ya instalado
) else (
    call npm install
)
cd ..

echo.
echo [4/4] Construyendo UI Tauri...
cd tauri-viz
call npx tauri build 2>nul
if errorlevel 1 (
    echo [WARN] Build UI fallo - se usara el modo consola
    echo Para la UI: instala Rust en https://rustup.rs
) else (
    echo [OK] UI construida en tauri-viz\src-tauri\target\release\
)
cd ..

echo.
echo ============================================
echo   SETUP COMPLETO
echo ============================================
echo.
echo Para usar:
echo   Master (tu):  run_master_ui.bat
echo   Worker (amigo): run_worker.bat  (con IP ajustada)
echo.
echo Alternativa si no compilo la UI:
echo   Master: python train_ddp.py (consola)
echo   Worker: python train_ddp.py (consola)
echo.
pause
