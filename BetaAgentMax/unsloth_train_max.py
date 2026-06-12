import json
import os
import sys
from typing import Any

# ── Environment ─────────────────────────────────────────────────────────────
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:256"
os.environ["OMP_NUM_THREADS"] = "12"
os.environ["MKL_NUM_THREADS"] = "12"

import torch

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cudnn.benchmark = True
torch.backends.cudnn.deterministic = False
torch.set_float32_matmul_precision("high")

from datasets import Dataset
from unsloth import FastLanguageModel, is_bfloat16_supported
from trl import SFTConfig, SFTTrainer
from unsloth_zoo.fused_losses.cross_entropy_loss import _get_chunk_multiplier

_get_chunk_multiplier.cache_clear()

# ── Config ──────────────────────────────────────────────────────────────────
# Qwen3-32B con thinking nativo + tool calling.
# Optimizado para A40 48GB en RunPod (QLoRA 4-bit + packing).
MODEL_NAME = "unsloth/Qwen3-32B-bnb-4bit"
MAX_SEQ_LENGTH = 4096
LORA_RANK = 32
LORA_ALPHA = 32
LORA_DROPOUT = 0
LEARNING_RATE = 1.5e-4
NUM_EPOCHS = 4
BATCH_SIZE = 2
GRADIENT_ACCUMULATION_STEPS = 8
OUTPUT_DIR = "outputs/AgentMax-qwen3-32b-v1.0"
SAVE_STEPS = 50

DATASET_DIR = os.path.join(os.path.dirname(__file__), "AgentMax-codetool-7b-v0.1/data")
TRAIN_PATH = os.path.join(DATASET_DIR, "train.jsonl")
VALID_PATH = os.path.join(DATASET_DIR, "valid.jsonl")


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
    return tokenizer.apply_chat_template(converted, tokenize=False, add_generation_prompt=False)


def build_dataset(path: str, tokenizer: Any) -> Dataset:
    raw = load_jsonl(path)
    texts = []
    for example in raw:
        try:
            text = format_conversation(example["messages"], tokenizer)
            texts.append({"text": text})
        except Exception as e:
            print(f"[WARN] {e}")
    ds = Dataset.from_list(texts)
    print(f"  {len(ds)} examples from {os.path.basename(path)}")
    return ds


def main():
    print("=" * 60)
    print(f"Model:       {MODEL_NAME}")
    print(f"LoRA:        rank={LORA_RANK}, alpha={LORA_ALPHA}")
    print(f"VRAM:        4-bit QLoRA, batch={BATCH_SIZE}, grad_accum={GRADIENT_ACCUMULATION_STEPS}")
    print(f"Device:      {torch.cuda.get_device_name() if torch.cuda.is_available() else 'CPU'}")
    print(f"VRAM:        {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
    print(f"TF32:        ON  | cuDNN bench: ON  | Workers: 8  | CPU: 12 cores")
    print("=" * 60)

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        max_seq_length=MAX_SEQ_LENGTH,
        dtype=None,
        load_in_4bit=True,
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_RANK,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    print("\n[Dataset]")
    train_ds = build_dataset(TRAIN_PATH, tokenizer)
    eval_ds = build_dataset(VALID_PATH, tokenizer)
    print(f"\nTrain: {len(train_ds)} | Valid: {len(eval_ds)}")
    if len(train_ds) == 0:
        sys.exit("ERROR: No training examples loaded.")

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
            warmup_ratio=0.03,
            num_train_epochs=NUM_EPOCHS,
            learning_rate=LEARNING_RATE,
            fp16=not is_bfloat16_supported(),
            bf16=is_bfloat16_supported(),
            logging_steps=5,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="cosine",
            seed=42,
            output_dir=OUTPUT_DIR,
            save_strategy="steps",
            save_steps=SAVE_STEPS,
            save_total_limit=3,
            eval_strategy="steps",
            eval_steps=SAVE_STEPS,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            report_to="none",
            dataloader_num_workers=4,
        ),
    )

    print("\n[Training]")
    trainer.train()

    model.save_pretrained(os.path.join(OUTPUT_DIR, "adapter"))
    tokenizer.save_pretrained(os.path.join(OUTPUT_DIR, "adapter"))
    print(f"\nAdapter saved to: {os.path.join(OUTPUT_DIR, 'adapter')}")


if __name__ == "__main__":
    main()
