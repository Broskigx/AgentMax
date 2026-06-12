import json
import os
import sys
from typing import Any

import torch
from datasets import Dataset
from unsloth import FastLanguageModel, is_bfloat16_supported
from trl import SFTConfig, SFTTrainer

# Disable torch._inductor to avoid Triton version mismatch on Windows
import torch._dynamo
torch._dynamo.config.disable = True

# Ensure fused loss cache is clear to avoid ZeroDivisionError
from unsloth_zoo.fused_losses.cross_entropy_loss import _get_chunk_multiplier
_get_chunk_multiplier.cache_clear()

# ── Config ──────────────────────────────────────────────────────────────────
MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"
MAX_SEQ_LENGTH = 4096
LORA_RANK = 32
LORA_ALPHA = 32
LORA_DROPOUT = 0
LEARNING_RATE = 2e-4
NUM_EPOCHS = 30
BATCH_SIZE = 2
GRADIENT_ACCUMULATION_STEPS = 4
OUTPUT_DIR = "outputs/AgentMax-7b-v1.0"
SAVE_STEPS = 20

DATASET_DIR = os.path.join(os.path.dirname(__file__), "AgentMax-codetool-7b-v0.1/data")
TRAIN_PATH = os.path.join(DATASET_DIR, "train.jsonl")
VALID_PATH = os.path.join(DATASET_DIR, "valid.jsonl")

# ── Helpers ─────────────────────────────────────────────────────────────────

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
            print(f"[WARN] Skipping example: {e}")
    ds = Dataset.from_list(texts)
    print(f"  Loaded {len(ds)} examples from {os.path.basename(path)}")
    return ds

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"Model:  {MODEL_NAME}")
    print(f"LoRA:   rank={LORA_RANK}, alpha={LORA_ALPHA}")
    print(f"VRAM:   4-bit QLoRA, batch_size={BATCH_SIZE}, grad_accum={GRADIENT_ACCUMULATION_STEPS}")
    print(f"Device: {torch.cuda.get_device_name() if torch.cuda.is_available() else 'CPU'}")
    print(f"VRAM:   {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
    print("=" * 60)

    # Load model + tokenizer (4-bit)
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        max_seq_length=MAX_SEQ_LENGTH,
        dtype=None,
        load_in_4bit=True,
    )

    # Attach LoRA
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

    # Dataset
    print("\n[Dataset]")
    train_ds = build_dataset(TRAIN_PATH, tokenizer)
    eval_ds = build_dataset(VALID_PATH, tokenizer)

    print(f"\nTrain: {len(train_ds)} | Valid: {len(eval_ds)}")
    if len(train_ds) == 0:
        sys.exit("ERROR: No training examples loaded.")
    print("\nSample training text:")
    print(train_ds[0]["text"][:500])
    print("...")

    # Trainer
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_ds,
        eval_dataset=eval_ds if len(eval_ds) > 0 else None,
        args=SFTConfig(
            dataset_text_field="text",
            max_seq_length=MAX_SEQ_LENGTH,
            dataset_num_proc=1,
            packing=False,
            per_device_train_batch_size=BATCH_SIZE,
            gradient_accumulation_steps=GRADIENT_ACCUMULATION_STEPS,
            warmup_steps=5,
            num_train_epochs=NUM_EPOCHS,
            learning_rate=LEARNING_RATE,
            fp16=not is_bfloat16_supported(),
            bf16=is_bfloat16_supported(),
            logging_steps=1,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=42,
            output_dir=OUTPUT_DIR,
            save_strategy="steps",
            save_steps=SAVE_STEPS,
            save_total_limit=3,
            report_to="none",
            dataloader_num_workers=0,
        ),
    )

    # Train
    print("\n[Training]")
    trainer.train()

    # Save adapter
    model.save_pretrained(os.path.join(OUTPUT_DIR, "adapter"))
    tokenizer.save_pretrained(os.path.join(OUTPUT_DIR, "adapter"))
    print(f"\nAdapter saved to: {os.path.join(OUTPUT_DIR, 'adapter')}")
    print("Done.")


if __name__ == "__main__":
    main()
