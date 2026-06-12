#!/usr/bin/env python3
"""
NixControl — Entrenamiento local/distribuido con auto-deteccion y resume entre rondas.
Soporta:
  - Modo local (1 GPU): --mode=local
  - Modo DDP (N GPUs): torchrun ... train_ddp.py
  - Resume de ronda anterior: --resume_adapter=path/to/adapter

Uso:
  torchrun --nnodes=N --nproc_per_node=1 --node_rank=RANK --master_addr=IP --master_port=PORT train_ddp.py
  python train_ddp.py --mode=local
"""
import argparse, json, os, sys, time
import torch
import torch.distributed as dist

parser = argparse.ArgumentParser()
parser.add_argument("--batch_size",                  type=int,   default=None)
parser.add_argument("--lora_rank",                   type=int,   default=None)
parser.add_argument("--max_seq_length",              type=int,   default=4096)
parser.add_argument("--num_workers",                 type=int,   default=None)
parser.add_argument("--learning_rate",               type=float, default=2e-4)
parser.add_argument("--gradient_accumulation_steps", type=int,   default=4)
parser.add_argument("--epochs",                      type=float, default=30.0)
parser.add_argument("--max_power",  action="store_true",
                    help="Activar todas las optimizaciones CUDA/cuDNN")
parser.add_argument("--power_target", type=float, default=90.0,
                    help="Porcentaje objetivo de uso real de CPU/GPU/VRAM (10-95, default 90)")
parser.add_argument("--no-auto",    action="store_false", dest="auto_config", default=True)
parser.add_argument("--mode",       choices=["ddp", "local"], default="ddp")
parser.add_argument("--resume_adapter", default=None,
                    help="Ruta al adapter de la ronda anterior para FedAvg resume")
args = parser.parse_args()

POWER_TARGET_PERCENT = max(10.0, min(float(args.power_target), 95.0))

def power_fraction() -> float:
    return POWER_TARGET_PERCENT / 100.0

def target_threads() -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(cpu_count, int((cpu_count * power_fraction()) + 0.999)))

# ── Optimizaciones CUDA siempre activas ─────────────────────────────────────
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF",
                      "expandable_segments:True,max_split_size_mb:512")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cudnn.benchmark = True
torch.backends.cudnn.deterministic = False
torch.set_float32_matmul_precision("high")

if args.max_power:
    os.environ["OMP_NUM_THREADS"] = str(target_threads())
    os.environ["MKL_NUM_THREADS"] = str(target_threads())
    os.environ["CUDA_DEVICE_MAX_CONNECTIONS"] = "8"

# Parche para Triton/Windows y cache de cross-entropy
import torch._dynamo
torch._dynamo.config.disable = True
try:
    from unsloth_zoo.fused_losses.cross_entropy_loss import _get_chunk_multiplier
    _get_chunk_multiplier.cache_clear()
except Exception:
    pass

from typing import Any
from datasets import Dataset
from unsloth import FastLanguageModel, is_bfloat16_supported
from trl import SFTConfig, SFTTrainer

MODEL_NAME  = "Qwen/Qwen2.5-7B-Instruct"
OUTPUT_DIR  = "outputs/NixControl-7b-v1.0"
SAVE_STEPS  = 20
DATASET_DIR = os.path.join(os.path.dirname(__file__), "NixControl-codetool-7b-v0.1", "data")
TRAIN_PATH  = os.path.join(DATASET_DIR, "train.jsonl")
VALID_PATH  = os.path.join(DATASET_DIR, "valid.jsonl")


def ts() -> str:
    return time.strftime("%H:%M:%S")

def log(tag: str, msg: str):
    print(f"[{ts()}][{tag}] {msg}", flush=True)


# ── Hardware detection ───────────────────────────────────────────────────────
def get_gpu_info() -> dict:
    if not torch.cuda.is_available():
        return {"gpu": "NONE", "vram_gb": 0, "cores": os.cpu_count() or 1}
    p = torch.cuda.get_device_properties(0)
    return {
        "gpu": p.name,
        "vram_gb": round(p.total_memory / 1e9, 1),
        "cores": os.cpu_count() or 1,
        "cuda": torch.version.cuda or "?",
    }

def compute_optimal_config(hw: list[dict]) -> dict:
    min_vram  = min(h["vram_gb"] for h in hw)
    total_vram = sum(h["vram_gb"] for h in hw)
    min_cores = min(h["cores"] for h in hw)
    weakest   = next(h for h in hw if h["vram_gb"] == min_vram)

    if args.max_power:
        if   min_vram >= 24: bs, lr = 8, 128
        elif min_vram >= 16: bs, lr = 4, 64
        elif min_vram >= 12: bs, lr = 4, 64
        elif min_vram >= 8:  bs, lr = 4, 64
        else:                bs, lr = 2, 32
    else:
        if   min_vram >= 10: bs, lr = 4, 64
        elif min_vram >= 8:  bs, lr = 2, 32
        elif min_vram >= 6:  bs, lr = 1, 16
        else:                bs, lr = 1, 8

    return {
        "batch_size":                  args.batch_size or bs,
        "lora_rank":                   args.lora_rank  or lr,
        "max_seq_length":              args.max_seq_length,
        "num_workers":                 args.num_workers or min(min_cores, 12, target_threads()),
        "learning_rate":               args.learning_rate,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "epochs":                      args.epochs,
        "min_vram":                    min_vram,
        "total_vram":                  total_vram,
        "num_gpus":                    len(hw),
        "weakest_gpu":                 weakest["gpu"],
    }


# ── Dataset ──────────────────────────────────────────────────────────────────
def load_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

def _ensure_openai_tool_format(m: dict) -> dict:
    out = {"role": m["role"], "content": m.get("content", "")}
    if m["role"] == "assistant" and m.get("tool_calls"):
        out["tool_calls"] = [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": tc["name"],
                          "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
            for i, tc in enumerate(m["tool_calls"])
        ]
    return out

def format_conversation(messages: list[dict], tokenizer: Any) -> str:
    converted = [_ensure_openai_tool_format(m) for m in messages]
    return tokenizer.apply_chat_template(converted, tokenize=False, add_generation_prompt=False)

def build_dataset(path: str, tokenizer: Any, is_main: bool) -> Dataset:
    raw = load_jsonl(path)
    texts = []
    skipped = 0
    for ex in raw:
        try:
            texts.append({"text": format_conversation(ex["messages"], tokenizer)})
        except Exception as e:
            if is_main:
                log("WARN", f"Ejemplo omitido: {e}")
            skipped += 1
    if is_main and skipped:
        log("WARN", f"{skipped} ejemplos omitidos de {os.path.basename(path)}")
    return Dataset.from_list(texts)


# ── Resume desde adapter (FedAvg entre rondas) ───────────────────────────────
def load_adapter_resume(model, adapter_path: str) -> bool:
    """
    Carga pesos de un adapter guardado en la ronda anterior.
    Compatible con PEFT (adapter_model.bin o safetensors).
    """
    try:
        bin_path = os.path.join(adapter_path, "adapter_model.bin")
        if not os.path.exists(bin_path):
            log("RESUME", f"No hay adapter_model.bin en {adapter_path}")
            return False

        from peft import set_peft_model_state_dict
        state_dict = torch.load(bin_path, map_location="cpu", weights_only=True)

        # Convertir a float32 si estan en fp16
        state_dict_f32 = {
            k: v.float() if v.is_floating_point() else v
            for k, v in state_dict.items()
        }

        result = set_peft_model_state_dict(model, state_dict_f32)
        params_m = sum(v.numel() for v in state_dict_f32.values()) / 1e6
        log("RESUME", f"Adapter cargado: {params_m:.1f}M params desde {adapter_path} ✓")
        return True

    except Exception as e:
        log("RESUME", f"Error cargando adapter: {e} — entrenando desde cero")
        return False


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    # DDP setup
    ddp_active = dist.is_available() and "LOCAL_RANK" in os.environ
    if ddp_active:
        dist.init_process_group(backend="gloo")  # gloo para multi-maquina, nccl para multi-GPU local
    is_main  = not ddp_active or dist.get_rank() == 0
    rank     = dist.get_rank()     if ddp_active else 0
    world    = dist.get_world_size() if ddp_active else 1

    # Hardware gather
    local_hw = get_gpu_info()
    all_hw   = [{} for _ in range(world)]  # Fix C1: unique dict per slot
    if ddp_active:
        dist.all_gather_object(all_hw, local_hw)
    else:
        all_hw[0] = local_hw

    if is_main:
        cfg = compute_optimal_config(all_hw)
        log("INFO", "=" * 60)
        log("INFO", f"Model: {MODEL_NAME}")
        log("INFO", f"Modo: {'DDP' if ddp_active else 'Local'} | Nodos: {world}")
        for i, h in enumerate(all_hw):
            lbl = "Master" if i == 0 else f"Worker-{i}"
            log("INFO", f"  [{lbl}] {h.get('gpu','?')} ({h.get('vram_gb',0)} GB, {h.get('cores',0)} cores)")
        log("INFO", f"Config: batch={cfg['batch_size']} rank={cfg['lora_rank']} "
            f"seq={cfg['max_seq_length']} workers={cfg['num_workers']}")
        log("INFO", f"GPU mas debil: {cfg['weakest_gpu']} ({cfg['min_vram']} GB) | "
            f"VRAM total: {cfg['total_vram']} GB")
        log("INFO", f"Resume adapter: {args.resume_adapter or 'No (desde cero)'}")
        log("INFO", f"Max power: {'SI' if args.max_power else 'NO'}")
        log("INFO", f"Potencia objetivo: {POWER_TARGET_PERCENT:.0f}% | threads objetivo: {target_threads()}")
        log("INFO", "=" * 60)
    else:
        cfg = {}

    if ddp_active:
        cfg_list = [cfg]
        dist.broadcast_object_list(cfg_list, src=0)
        cfg = cfg_list[0]

    if args.max_power:
        cfg["num_workers"] = min(int(cfg.get("num_workers", 0) or 0), target_threads())

    bs, lr_rank, seq, nw, lr_val, ga, ep = (
        cfg["batch_size"],   cfg["lora_rank"],    cfg["max_seq_length"],
        cfg["num_workers"],  cfg["learning_rate"], cfg["gradient_accumulation_steps"],
        cfg["epochs"],
    )

    # Cargar modelo en 4-bit
    if is_main:
        log("MODEL", f"Cargando {MODEL_NAME} en 4-bit QLoRA...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        max_seq_length=seq,
        dtype=None,
        load_in_4bit=True,
    )

    # Adjuntar LoRA
    model = FastLanguageModel.get_peft_model(
        model,
        r=lr_rank,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=lr_rank,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    # Resume desde ronda anterior (FedAvg)
    if args.resume_adapter and is_main:
        load_adapter_resume(model, args.resume_adapter)

    # Max power: activar configuraciones extra de torch
    if args.max_power:
        torch.set_num_threads(target_threads())
        try:
            torch.cuda.set_per_process_memory_fraction(power_fraction(), device=0)
        except Exception:
            pass

    # Dataset
    if is_main:
        log("DATA", "Cargando dataset...")
    train_ds = build_dataset(TRAIN_PATH, tokenizer, is_main)
    eval_ds  = build_dataset(VALID_PATH, tokenizer, is_main)
    if is_main:
        log("DATA", f"Train: {len(train_ds)} | Valid: {len(eval_ds)}")
    if len(train_ds) == 0:
        sys.exit("ERROR: No hay datos de entrenamiento")

    # Trainer
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_ds,
        eval_dataset=eval_ds if len(eval_ds) > 0 else None,
        args=SFTConfig(
            dataset_text_field="text",
            max_seq_length=seq,
            dataset_num_proc=min(nw, 4),
            packing=False,
            per_device_train_batch_size=bs,
            gradient_accumulation_steps=ga,
            warmup_steps=max(5, int(len(train_ds) * ep * 0.03 / (bs * ga))),
            num_train_epochs=ep,
            learning_rate=lr_val,
            fp16=not is_bfloat16_supported(),
            bf16=is_bfloat16_supported(),
            logging_steps=1,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="cosine",  # cosine decay: mejor para fine-tuning corto
            seed=42,
            output_dir=OUTPUT_DIR,
            save_strategy="steps",
            save_steps=SAVE_STEPS,
            save_total_limit=3,
            report_to="none",
            dataloader_num_workers=nw if not ddp_active else 0,
            ddp_find_unused_parameters=False if ddp_active else None,
            local_rank=int(os.environ.get("LOCAL_RANK", rank)) if ddp_active else -1,
            # Gradient checkpointing ya manejado por Unsloth
            gradient_checkpointing=False,
            # Mejor manejo de memoria
            group_by_length=True,
            length_column_name="length",
        ),
    )

    if is_main:
        log("TRAIN", "Iniciando entrenamiento...")
    t0 = time.time()
    trainer.train()
    elapsed = time.time() - t0

    if is_main:
        log("DONE", f"Entrenamiento completado en {elapsed/60:.1f} min")
        model.save_pretrained(os.path.join(OUTPUT_DIR, "adapter"))
        tokenizer.save_pretrained(os.path.join(OUTPUT_DIR, "adapter"))
        log("SAVE", f"Adapter guardado en: {os.path.join(OUTPUT_DIR, 'adapter')}")

        # Stats de VRAM
        if torch.cuda.is_available():
            peak = torch.cuda.max_memory_allocated(0) / 1e9
            log("VRAM", f"Peak VRAM usado: {peak:.2f} GB")


if __name__ == "__main__":
    main()
