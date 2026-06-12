@echo off
REM ============================================================
REM   PC1 (MASTER) - Launcher para entrenamiento DDP
REM   Antes: instala ZeroTier, únete a la red, dime tu IP virtual
REM ============================================================
cd /d "%~dp0"

REM ── Configura aquí ──────────────────────────────────────────
set MASTER_ADDR=192.168.196.1
set MASTER_PORT=12355
set NNODES=2
set NODE_RANK=0
REM ────────────────────────────────────────────────────────────

echo [AgentMax DDP] PC1 - MASTER (node_rank=%NODE_RANK%)
echo Conectando a %MASTER_ADDR%:%MASTER_PORT% ...
echo.

"C:\Users\agust\Desktop\Pruebas1\AgentMax\.venv\Scripts\torchrun.exe" ^
    --nnodes=%NNODES% ^
    --nproc_per_node=1 ^
    --node_rank=%NODE_RANK% ^
    --master_addr=%MASTER_ADDR% ^
    --master_port=%MASTER_PORT% ^
    train_ddp.py

pause
