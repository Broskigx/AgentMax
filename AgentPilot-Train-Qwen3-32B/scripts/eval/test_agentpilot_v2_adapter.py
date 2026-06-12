#!/usr/bin/env python3
"""
AgentMax V2 - prueba manual de un adapter/checkpoint con un prompt.

Carga el modelo base unsloth/Qwen3-VL-8B-Thinking, adjunta el adapter
indicado y devuelve UNICAMENTE la respuesta nueva del modelo (no el prompt).

Uso:
  python scripts/eval/test_AgentMax_v2_adapter.py \
      --adapter /workspace/.../adapter \
      --prompt "Mi servidor se cae cada 5 minutos, no tengo logs."

  python scripts/eval/test_AgentMax_v2_adapter.py \
      --adapter /workspace/.../checkpoint-50 \
      --prompt "..." \
      --max-new-tokens 512

  python scripts/eval/test_AgentMax_v2_adapter.py \
      --adapter ... --prompt "..." --base-only        # sin adapter (sanity)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

# Env tuning antes de torch
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


DEFAULT_SYSTEM = (
    "Eres AgentMax, un agente tecnico de AgentMax. Antes de actuar, "
    "razona internamente, pero nunca muestres razonamiento oculto ni "
    "etiquetas <think>. No inventes archivos, logs ni rutas. Pide evidencia "
    "antes de actuar. Pide confirmacion en acciones sensibles. Diferencia "
    "plan, permiso, ejecucion y resultado verificado."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--adapter", type=Path, required=True,
                   help="Ruta al adapter o a un checkpoint con peft adapter.")
    p.add_argument("--prompt", type=str, required=True,
                   help="Texto del usuario.")
    p.add_argument("--system", type=str, default=DEFAULT_SYSTEM)
    p.add_argument("--base-model", default="unsloth/Qwen3-VL-8B-Thinking")
    p.add_argument("--max-new-tokens", type=int, default=384)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--top-p", type=float, default=1.0)
    p.add_argument("--repetition-penalty", type=float, default=1.05)
    p.add_argument("--base-only", action="store_true",
                   help="No carga adapter, solo el modelo base (control).")
    p.add_argument("--no-4bit", action="store_true")
    p.add_argument("--print-prompt", action="store_true",
                   help="Imprime tambien el prompt aplicado (debug).")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not args.base_only and not args.adapter.exists():
        print(f"[ERROR] adapter no existe: {args.adapter}", file=sys.stderr)
        return 1

    # Imports diferidos para no cargar torch si argparse falla
    import torch  # noqa: E402
    from unsloth import FastVisionModel  # noqa: E402

    if not torch.cuda.is_available():
        print("[WARN] CUDA no disponible; este modelo necesita GPU para ir rapido.", file=sys.stderr)

    print(f"[load] base: {args.base_model}", file=sys.stderr)
    model, tokenizer = FastVisionModel.from_pretrained(
        model_name=args.base_model,
        load_in_4bit=not args.no_4bit,
    )

    if not args.base_only:
        print(f"[load] adapter: {args.adapter}", file=sys.stderr)
        try:
            model.load_adapter(str(args.adapter), adapter_name="AgentMax")
            model.set_adapter("AgentMax")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] no se pudo cargar adapter: {exc}", file=sys.stderr)
            return 2

    FastVisionModel.for_inference(model)

    messages = [
        {"role": "system", "content": args.system},
        {"role": "user", "content": args.prompt},
    ]
    prompt_text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )

    if args.print_prompt:
        print("--- PROMPT ---", file=sys.stderr)
        print(prompt_text, file=sys.stderr)
        print("--- /PROMPT ---", file=sys.stderr)

    inputs = tokenizer(
        text=[prompt_text],
        images=None,
        videos=None,
        return_tensors="pt",
    ).to(model.device)

    with torch.inference_mode():
        out_ids = model.generate(
            **inputs,
            max_new_tokens=args.max_new_tokens,
            do_sample=args.temperature > 0,
            temperature=args.temperature,
            top_p=args.top_p,
            repetition_penalty=args.repetition_penalty,
        )

    # Decodificar SOLO los tokens nuevos
    prompt_len = inputs["input_ids"].shape[1]
    new_tokens = out_ids[0, prompt_len:]
    answer = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    # Imprime solo la respuesta nueva en stdout
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
