"""
Training data exporter — converts resolved lessons into JSONL fine-tuning data.

Output format follows the OpenAI / Anthropic messages format so the file
can be used directly with most fine-tuning pipelines.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.learning.lesson_store import LessonStore

_EXPORT_DIR = Path("data") / "training"


async def export_jsonl(
    store: LessonStore,
    *,
    out_dir: Path = _EXPORT_DIR,
    resolved_only: bool = True,
    include_unresolved: bool = False,
) -> tuple[Path, int]:
    """
    Export lessons as JSONL training pairs.

    Returns:
        (output_path, record_count)
    """
    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    ts = int(time.time())
    out_path = out_dir / f"autolearn_{ts}.jsonl"

    rows = await store.list(limit=50_000)

    def _write() -> int:
        written = 0
        with out_path.open("w", encoding="utf-8") as f:
            for row in rows:
                if resolved_only and not row["resolved"] and not include_unresolved:
                    continue
                entry = _row_to_training_entry(row)
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1
        return written

    count = await asyncio.to_thread(_write)
    return out_path, count


def _row_to_training_entry(row: dict[str, Any]) -> dict[str, Any]:
    if row["action_type"]:
        context_line = f"[{row['context']} / {row['action_type']}]"
    else:
        context_line = f"[{row['context']}]"
    resolution_text = row.get("resolution") or "No explicit resolution recorded."

    user_content = (
        f"{context_line}\n"
        f"Input attempted: {row['input_summary']}\n"
        f"Error encountered: {row['error']}"
    )
    assistant_content = (
        f"This action failed with the error above.\n"
        f"Resolution: {resolution_text}\n"
        f"Status: {'resolved' if row['resolved'] else 'open'}"
    )

    return {
        "messages": [
            {"role": "user",      "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ],
        "metadata": {
            "source":      "agentmax_autolearn",
            "lesson_id":   row["id"],
            "context":     row["context"],
            "action_type": row["action_type"],
            "resolved":    bool(row["resolved"]),
            "ts":          row["ts"],
        },
    }
