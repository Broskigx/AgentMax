#!/usr/bin/env bash
# ============================================================
#  Comprime el adapter entrenado para descarga
#  Corre en el pod despues que termine train.py
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

ADAPTER="outputs/AgentMax-qwen3-32b-v1.0/adapter"
OUT="AgentMax-qwen3-32b-adapter-$(date +%Y%m%d).tar.gz"

if [ ! -d "$ADAPTER" ]; then
    echo "[ERROR] No existe $ADAPTER"
    echo "  El entrenamiento no termino o fallo."
    exit 1
fi

echo "Comprimiendo $ADAPTER -> $OUT"
tar czf "$OUT" -C "$(dirname "$ADAPTER")" "$(basename "$ADAPTER")"

SIZE=$(du -h "$OUT" | cut -f1)
echo ""
echo "============================================================"
echo "   LISTO:  $OUT  ($SIZE)"
echo "============================================================"
echo "   Descargalo desde RunPod -> Files -> click derecho -> download"
echo "   O via scp:"
echo "     scp -P <PUERTO> root@<IP>:$(pwd)/$OUT ."
echo "============================================================"
