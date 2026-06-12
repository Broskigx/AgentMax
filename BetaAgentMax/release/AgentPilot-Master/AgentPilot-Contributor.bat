@echo off
cd /d "%~dp0"
title NixControl Contributor

echo ============================================================
echo   NixControl CONTRIBUTOR v2.0
echo   Presta tu GPU para entrenar IA
echo ============================================================
echo.

set CONTRIB_EXE=tauri-viz\src-tauri\target\release\contributor.exe
if not exist "%CONTRIB_EXE%" (
    echo [ERROR] No se encuentra contributor.exe
    pause
    exit /b 1
)

echo Abriendo interfaz...
start "" "%CONTRIB_EXE%"
echo.
echo La interfaz se abrio. Ingresa la IP que te paso tu amigo.
echo Puedes cerrar esta ventana.
echo.
pause
echo.
echo La interfaz se abrio. Ingresa la IP que te paso tu amigo.
echo Puedes cerrar esta ventana.
echo.
pause
