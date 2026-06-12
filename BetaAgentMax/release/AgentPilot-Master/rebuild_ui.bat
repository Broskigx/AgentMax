@echo off
title NixControl - Rebuild UI Tauri
cd /d "%~dp0\tauri-viz"

echo ============================================
echo   Rebuild UI Tauri (incluye fixes v2.0)
echo ============================================
echo.
echo  Esto compila el codigo Rust + frontend con todos los fixes:
echo    - H4: find python en .venv correctamente
echo    - H5: mata procesos huerfanos al cerrar
echo    - H10: CSP definido
echo    - M10: stdout/stderr safe
echo    - UI nueva con tabs Hardware, Dispositivos
echo.
echo  Requiere: Rust + Node.js + npm
echo.

REM ─── Verificar herramientas ────────────────────────────────
where rustc >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Rust no instalado. Descarga: https://rustup.rs
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js/npm no instalado.
    pause
    exit /b 1
)

REM ─── npm install si node_modules no existe ────────────────
if not exist "node_modules\@tauri-apps\cli" (
    echo [1/2] Instalando dependencias npm...
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install fallo.
        pause
        exit /b 1
    )
)

REM ─── Tauri build ──────────────────────────────────────────
echo.
echo [2/2] Compilando Tauri (puede tardar 5-15 min)...
call npx tauri build --no-bundle
if errorlevel 1 (
    echo [ERROR] Tauri build fallo.
    pause
    exit /b 1
)

REM ─── Copiar tauri-viz.exe como contributor.exe ────────────
if exist "src-tauri\target\release\tauri-viz.exe" (
    copy /Y "src-tauri\target\release\tauri-viz.exe" "src-tauri\target\release\contributor.exe" >nul
    echo [OK] tauri-viz.exe y contributor.exe listos.
)

echo.
echo ============================================
echo   REBUILD COMPLETO
echo ============================================
echo.
echo Ahora podes:
echo   - Ejecutar NixControl-Master.bat (UI Master nueva)
echo   - Ejecutar build_release.bat (empaqueta para amigos)
echo.
pause
