#!/usr/bin/env python3
"""Build SFT JSONL from approved AgentMax logs only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APPROVED_DIR = ROOT / "data" / "AgentMax_logs" / "approved"
OUT_PATH = ROOT / "datasets" / "AgentMax_from_logs" / "AgentMax_logs_sft.jsonl"

SYSTEM = (
    "Eres AgentMax, un agente técnico de AgentMax. Responde con una decisión final breve, "
    "segura y verificable. No muestres razonamiento oculto ni etiquetas <think>. Si falta "
    "evidencia, pídela; no inventes archivos, rutas, logs ni resultados."
)


def iter_records(path: Path):
    files = [path] if path.is_file() else sorted(path.glob("*.jsonl"))
    for file in files:
        with file.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                yield json.loads(line)


def to_example(record: dict[str, Any]) -> dict[str, Any] | None:
    prompt = str(record.get("user_message") or record.get("prompt") or "").strip()
    response = str(
        record.get("sanitized_response") or record.get("assistant_response") or ""
    ).strip()
    outcome = str(record.get("outcome") or record.get("status") or "").lower()
    if not prompt or not response:
        return None
    if outcome not in {"approved", "success", "completed", "accepted"}:
        return None
    return {
        "category": "real_AgentMax_log",
        "risk_level": record.get("risk_level", "medium"),
        "skills": ["real_logs", "no_invention", "safety"],
        "metadata": {
            "source_session": record.get("session_id"),
            "tool_count": len(record.get("tool_calls") or []),
            "safety_flags": record.get("safety_flags") or [],
        },
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": response},
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=APPROVED_DIR)
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    examples = [example for record in iter_records(args.input) if (example := to_example(record))]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as fh:
        for example in examples:
            fh.write(json.dumps(example, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {len(examples)} examples -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
