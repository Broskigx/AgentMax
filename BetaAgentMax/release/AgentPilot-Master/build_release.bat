@echo off
title NixControl - Build Release
cd /d "%~dp0"

REM Atajo para correr el build script de PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_release.ps1" %*

echo.
pause
