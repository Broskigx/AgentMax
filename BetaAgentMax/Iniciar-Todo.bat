@echo off
cd /d "%~dp0"
title AgentMax - UN SOLO EXE

echo ============================================================
echo   AgentMax - UN SOLO EXE
echo   Auto-inicia servidor + GUI
echo ============================================================
echo.

set EXE=tauri-viz\src-tauri\target\release\tauri-viz.exe
if not exist "%EXE%" (
    echo [ERROR] No se encuentra: %EXE%
    echo Compila con: cd tauri-viz ^&^& npx tauri build
    pause
    exit /b 1
)

echo Iniciando...
echo   • Servidor coordinador (auto)
echo   • Dashboard grafico
echo.
start "" "%EXE%"
echo.
echo ┌──────────────────────────────────────────────────────────┐
echo │  LISTO                                                  │
echo │                                                         │
echo │  Comparte TU IP con tu amigo:                           │
ipconfig | findstr /C:"IPv4" | findstr /V "169.254"
echo │                                                         │
echo │  El corre en BetaAgentMax/contributor.exe               │
echo │                                                         │
echo │  Cierra la ventana grafica para detener todo.           │
echo └──────────────────────────────────────────────────────────┘
echo.
timeout /t 5 >nul
