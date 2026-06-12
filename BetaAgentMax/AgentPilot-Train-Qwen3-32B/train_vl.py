"""
AgentMax - Qwen3-VL-30B-A3B-Thinking Fine-Tuning
====================================================

VL = Vision-Language: el modelo CONSERVA su capacidad de ver imagenes
(no la entrenamos, solo le ensenamos tus tools de AgentMax).

Modelo: Qwen3-VL-30B-A3B-Thinking (MoE - 30B total, 3B activos/token)
  - Visión nativa ✅
  - Thinking <think>...</think> ✅
  - Tool calling nativo ✅
  - MoE = inference ULTRA rápida (~3B params activos)

Optimizado para A40 48GB en RunPod (QLoRA 4-bit + packing).
"""
import json
import os
import sys
from typing import Any

# ── Environment ─────────────────────────────────────────────────────────────
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:256"
os.environ["OMP_NUM_THREADS"] = "12"
os.environ["MKL_NUM_THREADS"] = "12"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import torch

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cudnn.benchmark = True
torch.backends.cudnn.deterministic = False
torch.set_float32_matmul_precision("high")

from datasets import Dataset
from unsloth import FastVisionModel, is_bfloat16_supported
from trl import SFTConfig, SFTTrainer

# ── Config ──────────────────────────────────────────────────────────────────
MODEL_NAME = "unsloth/Qwen3-VL-30B-A3B-Thinking-bnb-4bit"
MAX_SEQ_LENGTH = 4096
LORA_RANK = 32
LORA_ALPHA = 32
LORA_DROPOUT = 0
LEARNING_RATE = 1.5e-4
NUM_EPOCHS = 4
BATCH_SIZE = 2
GRADIENT_ACCUMULATION_STEPS = 8
WARMUP_RATIO = 0.03
WEIGHT_DECAY = 0.01

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(HERE, "data")
TRAIN_PATH = os.path.join(DATASET_DIR, "train.jsonl")
VALID_PATH = os.path.join(DATASET_DIR, "valid.jsonl")
OUTPUT_DIR = os.path.join(HERE, "outputs", "AgentMax-qwen3-vl-30b-v1.0")
SAVE_STEPS = 50
EVAL_STEPS = 50


def load_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _ensure_openai_tool_format(msg: dict) -> dict:
    m = {"role": msg["role"], "content": msg.get("content", "")}
    if msg["role"] == "assistant" and msg.get("tool_calls"):
        m["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                },
            }
            for i, tc in enumerate(msg["tool_calls"])
        ]
    return m


def format_conversation(messages: list[dict], tokenizer: Any) -> str:
    converted = [_ensure_openai_tool_format(m) for m in messages]
    return tokenizer.apply_chat_template(
        converted,
        tokenize=False,
        add_generation_prompt=False,
    )


def build_dataset(path: str, tokenizer: Any, name: str) -> Dataset:
    raw = load_jsonl(path)
    texts = []
    skipped = 0
    for example in raw:
        try:
            text = format_conversation(example["messages"], tokenizer)
            texts.append({"text": text})
        except Exception as e:
            skipped += 1
            if skipped < 5:
                print(f"  [WARN] skip: {e}")
    ds = Dataset.from_list(texts)
    print(f"  {name}: {len(ds)} examples  ({skipped} skipped)")
    return ds


def main():
    print("=" * 64)
    print("   AgentMax - Qwen3-VL-30B-A3B-Thinking Fine-Tuning")
    print("=" * 64)
    print(f"  Model:      {MODEL_NAME}")
    print(f"  Vision:     conservada (frozen, no se entrena)")
    print(f"  Language:   entrenado con LoRA r={LORA_RANK}")
    print(f"  Output:     {OUTPUT_DIR}")
    print(f"  Hyperparams:")
    print(f"    epochs={NUM_EPOCHS}  batch={BATCH_SIZE}  grad_accum={GRADIENT_ACCUMULATION_STEPS}")
    print(f"    lr={LEARNING_RATE}  seq_len={MAX_SEQ_LENGTH}")

    if not torch.cuda.is_available():
        sys.exit("[ERROR] No CUDA GPU detectada.")
    gpu = torch.cuda.get_device_properties(0)
    print(f"  Device:     {gpu.name}  ({gpu.total_memory / 1e9:.1f} GB VRAM)")
    print(f"  bfloat16:   {is_bfloat16_supported()}")
    print("=" * 64)

    print("\n[1/4] Cargando modelo (descarga ~20 GB la 1ra vez)...")
    model, tokenizer = FastVisionModel.from_pretrained(
        model_name=MODEL_NAME,
        load_in_4bit=True,
        use_gradient_checkpointing="unsloth",
    )

    print("\n[2/4] Aplicando LoRA (vision frozen, language trainable)...")
    model = FastVisionModel.get_peft_model(
        model,
        finetune_vision_layers=False,        # Frozen: conserva la vision nativa
        finetune_language_layers=True,        # Entrena: aprende tus tools
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
        r=LORA_RANK,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        random_state=42,
    )

    print("\n[3/4] Cargando dataset...")
    train_ds = build_dataset(TRAIN_PATH, tokenizer, "train")
    eval_ds = build_dataset(VALID_PATH, tokenizer, "valid")

    if len(train_ds) == 0:
        sys.exit("[ERROR] No training examples loaded.")

    print("\n[4/4] Entrenando...")
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_ds,
        eval_dataset=eval_ds if len(eval_ds) > 0 else None,
        args=SFTConfig(
            dataset_text_field="text",
            max_seq_length=MAX_SEQ_LENGTH,
            dataset_num_proc=4,
            packing=True,
            per_device_train_batch_size=BATCH_SIZE,
            gradient_accumulation_steps=GRADIENT_ACCUMULATION_STEPS,
            warmup_ratio=WARMUP_RATIO,
            num_train_epochs=NUM_EPOCHS,
            learning_rate=LEARNING_RATE,
            fp16=not is_bfloat16_supported(),
            bf16=is_bfloat16_supported(),
            logging_steps=5,
            optim="adamw_8bit",
            weight_decay=WEIGHT_DECAY,
            lr_scheduler_type="cosine",
            seed=42,
            output_dir=OUTPUT_DIR,
            save_strategy="steps",
            save_steps=SAVE_STEPS,
            save_total_limit=3,
            eval_strategy="steps" if len(eval_ds) > 0 else "no",
            eval_steps=EVAL_STEPS,
            load_best_model_at_end=len(eval_ds) > 0,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            report_to="none",
            dataloader_num_workers=4,
        ),
    )

    trainer.train()

    adapter_path = os.path.join(OUTPUT_DIR, "adapter")
    print(f"\n[5/5] Guardando adapter en {adapter_path}...")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)

    print("\n" + "=" * 64)
    print("   ENTRENAMIENTO COMPLETO")
    print("=" * 64)
    print(f"   Adapter en:  {adapter_path}")
    print(f"   El modelo CONSERVA su vision nativa Qwen3-VL.")
    print(f"   Para usar con imagenes: pasale 'image' en messages.")
    print("=" * 64)


if __name__ == "__main__":
    main()
