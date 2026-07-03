#!/usr/bin/env python3
"""
AutoConfigEngine — calcula la config optima de entrenamiento segun el hardware detectado.
Consultar via: python auto_config_engine.py --vram 8.6 --ram 32 --cores 12
O importar como modulo: from auto_config_engine import AutoConfigEngine
"""

import argparse
import json
import os

# Tabla: (vram_min_gb, vram_max_gb) -> (batch_size, lora_rank, num_workers, max_seq_len, grad_accum)
_VRAM_TABLE = [
    (0, 4, 1, 8, 2, 2048, 8),
    (4, 6, 1, 16, 4, 2048, 6),
    (6, 8, 1, 16, 4, 4096, 4),
    (8, 10, 2, 32, 8, 4096, 4),
    (10, 12, 4, 64, 8, 4096, 2),
    (12, 16, 4, 64, 12, 6144, 2),
    (16, 24, 8, 128, 16, 8192, 1),
    (24, 999, 8, 128, 16, 8192, 1),
]


class AutoConfigEngine:
    def __init__(
        self, vram_gb: float, ram_gb: float, cores: int, tflops_fp16: float = 0, gpu_name: str = ""
    ):
        self.vram_gb = vram_gb
        self.ram_gb = ram_gb
        self.cores = cores
        self.tflops_fp16 = tflops_fp16
        self.gpu_name = gpu_name

    def _lookup_vram(self):
        for vmin, vmax, bs, rank, workers, seq, ga in _VRAM_TABLE:
            if vmin <= self.vram_gb < vmax:
                return bs, rank, workers, seq, ga
        return 1, 8, 2, 2048, 8

    def compute(self, max_power: bool = False) -> dict:
        bs, rank, workers, seq, ga = self._lookup_vram()

        # Ajustar workers a CPU disponible
        workers = min(workers, max(1, self.cores - 1))

        # RAM baja → reducir seq y ga
        if self.ram_gb < 16:
            seq = min(seq, 2048)
            ga = max(ga, 4)
        elif self.ram_gb < 32:
            seq = min(seq, 4096)

        # Max power mode: usar todos los recursos
        if max_power:
            bs = max(bs, min(bs * 2, 8))
            rank = min(rank * 2, 128)
            workers = min(self.cores, 16)

        # Cuantificar potencia GPU para ETA estimada
        # ~1M tokens/hora por TFLOPS FP16 (aprox muy conservador)
        tflops = self.tflops_fp16 or 8.0  # fallback si no hay benchmark
        tokens_per_hour = tflops * 800_000
        # Dataset típico: 400 ejemplos * seq_len promedio * epochs
        assumed_tokens = 400 * (seq // 2) * 3
        eta_hours = assumed_tokens / tokens_per_hour if tokens_per_hour > 0 else 99

        # Sugerencias adicionales
        use_tf32 = self.tflops_fp16 > 0 or "3090" in self.gpu_name or "4090" in self.gpu_name
        use_cudnn = True

        return {
            "batch_size": bs,
            "lora_rank": rank,
            "num_workers": workers,
            "max_seq_length": seq,
            "gradient_accumulation_steps": ga,
            "learning_rate": 2e-4,
            "epochs": 3,
            "use_tf32": use_tf32,
            "use_cudnn_benchmark": use_cudnn,
            "effective_batch_size": bs * ga,
            "eta_hours_estimate": round(eta_hours, 1),
            "vram_headroom_gb": round(self.vram_gb * 0.15, 1),
            "warnings": self._warnings(bs, seq),
        }

    def _warnings(self, bs: int, seq: int) -> list[str]:
        w = []
        if self.vram_gb < 4:
            w.append("VRAM muy baja (<4 GB) — el entrenamiento puede fallar por OOM")
        if self.vram_gb < 8 and seq >= 4096:
            w.append("seq_length alto para tu VRAM — considera reducir a 2048")
        if self.ram_gb < 8:
            w.append("RAM baja (<8 GB) — puede causar swapping lento")
        if self.cores < 4:
            w.append("Pocos nucleos CPU — los dataloaders pueden ser cuello de botella")
        return w

    def summary(self) -> str:
        cfg = self.compute()
        lines = [
            "=== Config Optima ===",
            f"  Batch/GPU:  {cfg['batch_size']}",
            f"  LoRA rank:  {cfg['lora_rank']}",
            f"  Workers:    {cfg['num_workers']}",
            f"  Max seq:    {cfg['max_seq_length']}",
            f"  Grad accum: {cfg['gradient_accumulation_steps']}",
            f"  Batch eff.: {cfg['effective_batch_size']}",
            f"  ETA est.:   ~{cfg['eta_hours_estimate']}h",
        ]
        if cfg["warnings"]:
            lines.append("  Advertencias:")
            for w in cfg["warnings"]:
                lines.append(f"    ⚠ {w}")
        return "\n".join(lines)


def from_hardware_scan(hw: dict, max_power: bool = False) -> dict:
    """Recibe el dict de full_scan() de hardware_detective.py y devuelve la config."""
    gpu = hw.get("gpu", {})
    cpu = hw.get("cpu", {})
    ram = hw.get("ram", {})
    bench = hw.get("benchmark", {})

    engine = AutoConfigEngine(
        vram_gb=gpu.get("vram_free_gb", gpu.get("vram_total_gb", 0)),
        ram_gb=ram.get("total_gb", 0),
        cores=cpu.get("cores_logical", os.cpu_count() or 4),
        tflops_fp16=bench.get("tflops_fp16", 0),
        gpu_name=gpu.get("gpu", ""),
    )
    return engine.compute(max_power=max_power)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--vram", type=float, required=True, help="VRAM libre en GB")
    p.add_argument("--ram", type=float, default=16, help="RAM total en GB")
    p.add_argument("--cores", type=int, default=8, help="Nucleos logicos CPU")
    p.add_argument("--tflops", type=float, default=0, help="TFLOPS FP16 del benchmark")
    p.add_argument("--gpu", default="", help="Nombre GPU")
    p.add_argument("--max-power", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    engine = AutoConfigEngine(args.vram, args.ram, args.cores, args.tflops, args.gpu)
    cfg = engine.compute(max_power=args.max_power)

    if args.json:
        print(json.dumps(cfg, ensure_ascii=False))
    else:
        print(engine.summary())
        print(f"\n[CONFIG_JSON] {json.dumps(cfg, ensure_ascii=False)}", flush=True)
