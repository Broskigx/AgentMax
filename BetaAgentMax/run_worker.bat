@echo off
title AgentMax - Worker
cd /d "%~dp0"

set MASTER_IP=192.168.196.1
set MASTER_PORT=12355
set VENV_PYTHON=..\.venv\Scripts\torchrun.exe

echo ============================================
echo   AgentMax - WORKER (DDP clasico)
echo   Conectando a master: %MASTER_IP%:%MASTER_PORT%
echo ============================================

timeout /t 2 /nobreak >nul

ping -n 1 %MASTER_IP% >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] No se puede alcanzar %MASTER_IP%
    echo Verifica ZeroTier y la IP
    pause
    exit /b 1
)

echo [OK] Master alcanzable
echo.
echo [TRAIN] Iniciando worker...
%VENV_PYTHON% --nnodes=2 --nproc_per_node=1 --node_rank=1 --master_addr=%MASTER_IP% --master_port=%MASTER_PORT% train_ddp.py

echo [FIN] Worker desconectado
pause
