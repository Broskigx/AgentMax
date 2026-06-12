@echo off
title AgentMax - Contribuyente
cd /d "%~dp0"
echo ============================================
echo   AgentMax - Contribuir GPU al Federado
echo   BetaAgentMax
echo ============================================
echo.
set /p COORD_IP="IP del coordinador (ej. 192.168.196.1): "
set /p COORD_PORT="Puerto (Enter=12356): "
if "%COORD_PORT%"=="" set COORD_PORT=12356

echo.
echo Conectando a %COORD_IP%:%COORD_PORT%...
echo Tu GPU contribuira al entrenamiento federado.
echo.

python contributor.py --coordinator_ip %COORD_IP% --coordinator_port %COORD_PORT% --train_dir "%CD%"

echo Desconectado.
pause
