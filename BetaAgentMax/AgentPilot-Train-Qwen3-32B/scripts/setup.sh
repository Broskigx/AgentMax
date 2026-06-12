#!/usr/bin/env bash
# ============================================================
#  AgentMax - Setup deps (Linux + CUDA 12.x)
#  Usalo en RunPod o cualquier pod con GPU
# ============================================================
set -euo pipefail

echo "============================================================"
echo "   AgentMax - Setup dependencias"
echo "============================================================"

# --- GPU info ---
echo "[1/4] GPU:"
if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "  [ERROR] nvidia-smi no encontrado."
    exit 1
fi
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

# --- HF cache en /workspace para persistencia ---
echo "[2/4] Configurando cache..."
export HF_HOME="${HF_HOME:-/workspace/hf_cache}"
export TRANSFORMERS_CACHE="$HF_HOME/transformers"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
mkdir -p "$HF_HOME"

# Persistir en bashrc para futuras sesiones
if ! grep -q "HF_HOME=/workspace/hf_cache" ~/.bashrc 2>/dev/null; then
    {
        echo "export HF_HOME=/workspace/hf_cache"
        echo "export TRANSFORMERS_CACHE=/workspace/hf_cache/transformers"
        echo "export HF_DATASETS_CACHE=/workspace/hf_cache/datasets"
    } >> ~/.bashrc
fi
echo "  HF_HOME: $HF_HOME"

# --- Python deps ---
echo "[3/4] Instalando dependencias (3-8 min la 1ra vez)..."
python -m pip install --upgrade pip --quiet

# unsloth desde main para soporte Qwen3
python -m pip install --quiet \
    "unsloth @ git+https://github.com/unslothai/unsloth.git" \
    || python -m pip install --quiet unsloth

python -m pip install --quiet \
    "trl>=0.12,<0.14" \
    datasets \
    "transformers>=4.46" \
    peft \
    accelerate \
    bitsandbytes \
    safetensors \
    sentencepiece \
    protobuf \
    psutil

# --- Sanity check ---
echo "[4/4] Verificando imports..."
python - <<'PY'
import torch, unsloth, trl, datasets, transformers, peft
print(f"  torch        {torch.__version__}")
print(f"  CUDA         {torch.version.cuda}  | available={torch.cuda.is_available()}")
print(f"  unsloth      {unsloth.__version__}")
print(f"  trl          {trl.__version__}")
print(f"  transformers {transformers.__version__}")
print(f"  peft         {peft.__version__}")
PY

echo ""
echo "============================================================"
echo "   SETUP OK - Para entrenar:"
echo "     bash scripts/train.sh"
echo "   O directamente:"
echo "     python train.py"
echo "============================================================"
