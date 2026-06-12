@echo off
title AgentMax - MASTER (Beta)
cd /d "%~dp0"
echo ============================================
echo   AgentMax MASTER - BetaAgentMax
echo ============================================
echo.
echo Directorio: %CD%
echo El .venv se encuentra en: %CD%\..\.venv
echo.
echo Abriendo interfaz Master...
echo.

start "" "%~dp0tauri-viz\src-tauri\target\release\tauri-viz.exe"

echo.
echo Interfaz abierta. Selecciona la pestana que necesites:
echo   - Coordinador: para entrenar con MUCHOS contribuyentes
echo   - DDP: para entrenar con 1 amigo via ZeroTier
echo.
echo En "Directorio training" pon: %CD%
echo En "Python venv" pon: ..\.venv\Scripts\torchrun.exe
echo.
echo Comparte con tus amigos:
echo   - El archivo run_contributor_max.bat
echo   - La carpeta completa BetaAgentMax
echo.
pause
