#!/usr/bin/env bash
# ============================================================
#  AgentMax - RunPod Setup & Train Launcher
#  Target: A40 48GB / RTX 6000 Ada / A100 (Linux + CUDA 12.x)
#  Modelo: Qwen3-32B con thinking, QLoRA 4-bit
# ============================================================
set -euo pipefail

echo "============================================================"
echo "   AgentMax - RunPod Qwen3-32B Trainer"
echo "============================================================"

cd "$(dirname "$0")"

# --- [1] GPU info ------------------------------------------------------------
echo "[1/5] Verificando GPU..."
if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "  [ERROR] nvidia-smi no encontrado. Estas en un pod con GPU?"
    exit 1
fi
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

# --- [2] Cache & workspace ---------------------------------------------------
echo "[2/5] Configurando paths..."
export HF_HOME="${HF_HOME:-/workspace/hf_cache}"
export TRANSFORMERS_CACHE="$HF_HOME/transformers"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
mkdir -p "$HF_HOME" outputs
echo "  HF cache:    $HF_HOME"
echo "  Outputs:     $(pwd)/outputs"

# --- [3] Python deps ---------------------------------------------------------
echo "[3/5] Instalando dependencias (tarda 3-8 min la 1ra vez)..."
python -m pip install --upgrade pip --quiet

# Torch ya viene en la imagen runpod/pytorch, solo instalamos extras
python -m pip install --quiet \
    "unsloth[cu124-torch240] @ git+https://github.com/unslothai/unsloth.git" \
    || python -m pip install --quiet unsloth

python -m pip install --quiet \
    trl==0.12.* \
    datasets \
    transformers \
    peft \
    accelerate \
    bitsandbytes \
    safetensors \
    sentencepiece \
    protobuf \
    psutil

echo "  [OK] Dependencias listas"

# --- [4] Sanity check --------------------------------------------------------
echo "[4/5] Verificando imports..."
python -c "
import torch, unsloth, trl, datasets, transformers, peft
print(f'  torch {torch.__version__} | CUDA {torch.version.cuda} | GPU OK = {torch.cuda.is_available()}')
print(f'  unsloth {unsloth.__version__} | trl {trl.__version__} | transformers {transformers.__version__}')
" || { echo "  [ERROR] Faltan deps"; exit 1; }

# --- [5] Lanzar entrenamiento ------------------------------------------------
echo "[5/5] Lanzando unsloth_train_max.py (Qwen3-32B, 4 epochs, 4000 samples)..."
echo "============================================================"
echo "  Logs en vivo abajo. Para parar: Ctrl+C"
echo "  Si se desconecta el SSH, los logs siguen en train.log:"
echo "    tail -f train.log"
echo "============================================================"

# nohup para sobrevivir si se cae el SSH
python -u unsloth_train_max.py 2>&1 | tee train.log
