#!/usr/bin/env bash
# ============================================================
#  AgentMax - Lanzador de entrenamiento
#  Corre train.py con log a archivo + tmux-friendly
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

export HF_HOME="${HF_HOME:-/workspace/hf_cache}"
export TRANSFORMERS_CACHE="$HF_HOME/transformers"
export HF_DATASETS_CACHE="$HF_HOME/datasets"

STAMP=$(date +%Y%m%d-%H%M%S)
LOG="train-${STAMP}.log"

echo "============================================================"
echo "  Lanzando entrenamiento. Log: $LOG"
echo "  Tip: para sobrevivir a desconexion SSH usa tmux:"
echo "    tmux new -s train"
echo "    bash scripts/train.sh"
echo "    Ctrl+B, D       (detach)"
echo "    tmux attach -t train  (reconectar)"
echo "============================================================"

python -u train.py 2>&1 | tee "$LOG"
