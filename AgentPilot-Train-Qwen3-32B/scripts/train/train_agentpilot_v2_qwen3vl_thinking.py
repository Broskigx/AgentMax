#!/usr/bin/env python3
"""
AgentMax V2 - Qwen3-VL-8B-Thinking Fine-Tuning (SFT, LoRA/QLoRA)
==================================================================

Objetivo:
  Reentrenar AgentMax para que:
    - razone internamente sin emitir <think>/</think>;
    - no invente archivos, logs, rutas ni resultados;
    - pida evidencia antes de actuar;
    - pida confirmacion en acciones sensibles;
    - diferencie plan, permiso, ejecucion y resultado verificado.

Decisiones clave de este run (priorizar estabilidad sobre velocidad):
  - Modelo base:        unsloth/Qwen3-VL-8B-Thinking
  - Formato:            conversacional `messages` (system/user/assistant)
  - PEFT:               LoRA r=32, alpha=32, dropout=0
  - QLoRA:              load_in_4bit=True
  - Epochs:             1  (no 4)
  - Learning rate:      2e-5
  - Warmup ratio:       0.03
  - Weight decay:       0.0
  - Seq length:         2048
  - Batch effective:    2 * 8 = 16
  - save_steps:         50  (sin save_total_limit -> conserva TODOS los checkpoints)
  - eval_steps:         50
  - logging_steps:      10
  - Seed:               42
  - Output dir:         /workspace/AgentMax-Train-Qwen3-32B/outputs/AgentMax-v2-qwen3vl-thinking
  - Adapter final:      <output_dir>/adapter

Reglas defensivas:
  - NO toca el adapter viejo
  - NO usa el output_dir anterior
  - NO exporta GGUF
  - NO borra checkpoints
  - load_best_model_at_end=False  (queremos los checkpoints intermedios)

Uso:
  python scripts/train/train_AgentMax_v2_qwen3vl_thinking.py \
      --train datasets/AgentMax_v2/train.jsonl \
      --eval  datasets/AgentMax_v2/validation.jsonl \
      --output-dir /workspace/AgentMax-Train-Qwen3-32B/outputs/AgentMax-v2-qwen3vl-thinking

Dependencias (en RunPod ya estan instaladas):
  pip install "unsloth[colab-new]" unsloth_zoo
  pip install "torchvision>=0.27.0" "trl>=0.18.2,<=0.24.0" "datasets>=3.4.1,<4.4.0"
  pip install peft transformers accelerate bitsandbytes
"""
from __future__ import annotations

# ── Stdlib ──────────────────────────────────────────────────────────────────
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# ── Environment (debe ir ANTES de importar torch/unsloth) ───────────────────
os.environ.setdefault(
    "PYTORCH_CUDA_ALLOC_CONF",
    "expandable_segments:True,max_split_size_mb:256",
)
os.environ.setdefault("OMP_NUM_THREADS", "12")
os.environ.setdefault("MKL_NUM_THREADS", "12")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# NOTE: ML stack (torch / unsloth / trl / datasets) se importa DENTRO de main()
# para que `--help` y `py_compile` no requieran GPU / unsloth instalados.


# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_MODEL_NAME = "unsloth/Qwen3-VL-8B-Thinking"
DEFAULT_OUTPUT_DIR = (
    "/workspace/AgentMax-Train-Qwen3-32B/outputs/AgentMax-v2-qwen3vl-thinking"
)

# Hiperparametros de estabilidad
MAX_SEQ_LENGTH = 2048
LORA_RANK = 32
LORA_ALPHA = 32
LORA_DROPOUT = 0
LEARNING_RATE = 2e-5
NUM_EPOCHS = 1
BATCH_SIZE = 2
GRADIENT_ACCUMULATION_STEPS = 8
WARMUP_RATIO = 0.03
WEIGHT_DECAY = 0.0
SAVE_STEPS = 50
EVAL_STEPS = 50
LOGGING_STEPS = 10
SEED = 42


# ── Dataset helpers ─────────────────────────────────────────────────────────

def _ensure_openai_tool_format(msg: dict) -> dict:
    """Soporta `tool_calls` opcional manteniendo el resto del mensaje intacto."""
    m = {"role": msg["role"], "content": msg.get("content", "")}
    if msg["role"] == "assistant" and msg.get("tool_calls"):
        m["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                },
            }
            for i, tc in enumerate(msg["tool_calls"])
        ]
    return m


def _format_conversation(messages: list[dict], tokenizer: Any) -> str:
    converted = [_ensure_openai_tool_format(m) for m in messages]
    return tokenizer.apply_chat_template(
        converted,
        tokenize=False,
        add_generation_prompt=False,
    )


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_dataset(path: Path, tokenizer: Any, label: str) -> Any:
    from datasets import Dataset  # local import
    raw = _load_jsonl(path)
    texts: list[dict[str, str]] = []
    skipped = 0
    for example in raw:
        msgs = example.get("messages")
        if not isinstance(msgs, list) or not msgs:
            skipped += 1
            continue
        try:
            text = _format_conversation(msgs, tokenizer)
            if not text.strip():
                skipped += 1
                continue
            texts.append({"text": text})
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            if skipped < 5:
                print(f"  [WARN] skip ({label}): {exc}")
    ds = Dataset.from_list(texts)
    print(f"  {label}: {len(ds)} examples  ({skipped} skipped)")
    return ds


# ── Main ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve()
    repo_root = here.parents[2]  # AgentMax/
    default_train = repo_root / "datasets" / "AgentMax_v2" / "train.jsonl"
    default_eval = repo_root / "datasets" / "AgentMax_v2" / "validation.jsonl"

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default=DEFAULT_MODEL_NAME, help="Base model name")
    p.add_argument("--train", type=Path, default=default_train, help="train.jsonl")
    p.add_argument("--eval", dest="eval_path", type=Path, default=default_eval, help="validation.jsonl")
    p.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT_DIR))
    p.add_argument("--max-seq-length", type=int, default=MAX_SEQ_LENGTH)
    p.add_argument("--lora-rank", type=int, default=LORA_RANK)
    p.add_argument("--lora-alpha", type=int, default=LORA_ALPHA)
    p.add_argument("--lr", type=float, default=LEARNING_RATE)
    p.add_argument("--epochs", type=float, default=NUM_EPOCHS)
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--grad-accum", type=int, default=GRADIENT_ACCUMULATION_STEPS)
    p.add_argument("--save-steps", type=int, default=SAVE_STEPS)
    p.add_argument("--eval-steps", type=int, default=EVAL_STEPS)
    p.add_argument("--logging-steps", type=int, default=LOGGING_STEPS)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--no-eval", action="store_true", help="Skip the validation pass")
    p.add_argument("--resume", action="store_true",
                   help="Resume from the most recent checkpoint inside output_dir, if any")
    p.add_argument("--no-4bit", action="store_true",
                   help="Disable 4-bit (debug only; will OOM on A40)")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # ── Lazy ML imports (skip them for --help / py_compile) ─────────────────
    import torch  # noqa: E402
    from unsloth import FastVisionModel, is_bfloat16_supported  # noqa: E402
    from trl import SFTConfig, SFTTrainer  # noqa: E402
    from datasets import Dataset  # noqa: E402

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.deterministic = False
    torch.set_float32_matmul_precision("high")

    print("=" * 70)
    print("   AgentMax V2 - Qwen3-VL-8B-Thinking SFT")
    print("=" * 70)
    print(f"  Model:        {args.model}")
    print(f"  Train:        {args.train}")
    print(f"  Eval:         {args.eval_path}")
    print(f"  Output dir:   {args.output_dir}")
    print(f"  Hyperparams:")
    print(f"    epochs={args.epochs}  lr={args.lr}  seq_len={args.max_seq_length}")
    print(f"    batch={args.batch_size}  grad_accum={args.grad_accum}")
    print(f"    save_steps={args.save_steps}  eval_steps={args.eval_steps}  logging_steps={args.logging_steps}")
    print(f"    lora_r={args.lora_rank}  lora_alpha={args.lora_alpha}  seed={args.seed}")
    print("=" * 70)

    # ── Sanity checks ───────────────────────────────────────────────────────
    if not args.train.exists():
        sys.exit(f"[ERROR] Train file not found: {args.train}")
    if not args.no_eval and not args.eval_path.exists():
        sys.exit(f"[ERROR] Eval file not found: {args.eval_path}")
    if not torch.cuda.is_available():
        sys.exit("[ERROR] CUDA not available - this script needs a GPU.")

    gpu = torch.cuda.get_device_properties(0)
    print(f"  Device:       {gpu.name}  ({gpu.total_memory / 1e9:.1f} GB VRAM)")
    print(f"  bfloat16:     {is_bfloat16_supported()}")
    print("=" * 70)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    adapter_path = args.output_dir / "adapter"

    if adapter_path.exists() and any(adapter_path.iterdir()) and not args.resume:
        print(f"[WARN] {adapter_path} already exists and is not empty.")
        print("       This script will NOT overwrite it. Move/rename it, or pass --resume.")
        sys.exit(2)

    # ── Load model ──────────────────────────────────────────────────────────
    print("\n[1/4] Loading base model...")
    model, tokenizer = FastVisionModel.from_pretrained(
        model_name=args.model,
        load_in_4bit=not args.no_4bit,
        use_gradient_checkpointing="unsloth",
    )

    # ── Apply LoRA (vision frozen) ──────────────────────────────────────────
    print("\n[2/4] Applying LoRA (vision frozen, language trainable)...")
    model = FastVisionModel.get_peft_model(
        model,
        finetune_vision_layers=False,        # conserva la vision nativa
        finetune_language_layers=True,        # entrena: aprende el formato AgentMax
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        random_state=args.seed,
    )

    # ── Datasets ────────────────────────────────────────────────────────────
    print("\n[3/4] Building datasets...")
    train_ds = build_dataset(args.train, tokenizer, "train")
    eval_ds = (
        build_dataset(args.eval_path, tokenizer, "eval")
        if not args.no_eval else Dataset.from_list([])
    )
    if len(train_ds) == 0:
        sys.exit("[ERROR] No training examples loaded.")

    # ── Trainer config ──────────────────────────────────────────────────────
    print("\n[4/4] Configuring trainer...")
    has_eval = len(eval_ds) > 0
    cfg = SFTConfig(
        dataset_text_field="text",
        max_seq_length=args.max_seq_length,
        dataset_num_proc=4,
        packing=False,  # processor-based VL model; packing causes seq_len issues
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        warmup_ratio=WARMUP_RATIO,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        fp16=not is_bfloat16_supported(),
        bf16=is_bfloat16_supported(),
        logging_steps=args.logging_steps,
        optim="adamw_8bit",
        weight_decay=WEIGHT_DECAY,
        lr_scheduler_type="cosine",
        seed=args.seed,
        output_dir=str(args.output_dir),
        save_strategy="steps",
        save_steps=args.save_steps,
        # save_total_limit deliberadamente AUSENTE -> conservar todos los checkpoints
        eval_strategy="steps" if has_eval else "no",
        eval_steps=args.eval_steps if has_eval else None,
        load_best_model_at_end=False,  # mantenemos checkpoints intermedios sin sobrescribir
        report_to="none",
        dataloader_num_workers=4,
        gradient_checkpointing=True,
    )

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_ds,
        eval_dataset=eval_ds if has_eval else None,
        args=cfg,
    )

    # ── Train ───────────────────────────────────────────────────────────────
    print("\n[5/5] Training...")
    trainer.train(resume_from_checkpoint=args.resume)

    # ── Save final adapter ──────────────────────────────────────────────────
    print(f"\nSaving final adapter to {adapter_path}...")
    model.save_pretrained(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))

    # Persist exact hyperparams used for this run (for the eval script)
    run_meta = {
        "model": args.model,
        "train": str(args.train),
        "eval": str(args.eval_path) if not args.no_eval else None,
        "output_dir": str(args.output_dir),
        "max_seq_length": args.max_seq_length,
        "lora_rank": args.lora_rank,
        "lora_alpha": args.lora_alpha,
        "lr": args.lr,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "grad_accum": args.grad_accum,
        "save_steps": args.save_steps,
        "eval_steps": args.eval_steps,
        "logging_steps": args.logging_steps,
        "seed": args.seed,
    }
    (args.output_dir / "run_metadata.json").write_text(
        json.dumps(run_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n" + "=" * 70)
    print("   TRAINING COMPLETE")
    print("=" * 70)
    print(f"   Adapter:        {adapter_path}")
    print(f"   Checkpoints:    {args.output_dir} (kept intact, every {args.save_steps} steps)")
    print(f"   Run metadata:   {args.output_dir / 'run_metadata.json'}")
    print()
    print("   Next step (NO GGUF until eval passes):")
    print(f"     python scripts/eval/eval_AgentMax_v2_checkpoints.py "
          f"--run-dir {args.output_dir}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
