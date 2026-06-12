from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = [ROOT / "data" / "train.jsonl", ROOT / "data" / "valid.jsonl"]


def iter_rows():
    for path in DATA_FILES:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if line:
                    yield path.name, json.loads(line)


def approx_len(row: dict[str, Any]) -> int:
    return len(json.dumps(row, ensure_ascii=False))


def main() -> int:
    total = 0
    by_file = Counter()
    by_category = Counter()
    tools = Counter()
    examples_without_tool = 0
    examples_with_confirmation = 0
    lengths: list[int] = []

    for file_name, row in iter_rows():
        total += 1
        by_file[file_name] += 1
        by_category[row.get("category", "<missing>")] += 1
        lengths.append(approx_len(row))
        used_any_tool = False
        used_confirmation = False
        for message in row.get("messages", []):
            if not isinstance(message, dict):
                continue
            for call in message.get("tool_calls", []) or []:
                if not isinstance(call, dict):
                    continue
                name = call.get("name")
                if isinstance(name, str):
                    tools[name] += 1
                    used_any_tool = True
                    if name == "ask_confirmation":
                        used_confirmation = True
        if not used_any_tool:
            examples_without_tool += 1
        if used_confirmation:
            examples_with_confirmation += 1

    avg_len = int(sum(lengths) / total) if total else 0
    print(f"total_examples: {total}")
    print("by_file:")
    for key, value in sorted(by_file.items()):
        print(f"  {key}: {value}")
    print("by_category:")
    for key, value in sorted(by_category.items()):
        print(f"  {key}: {value}")
    print("tools_most_used:")
    for key, value in tools.most_common():
        print(f"  {key}: {value}")
    print(f"examples_without_tool: {examples_without_tool}")
    print(f"examples_with_ask_confirmation: {examples_with_confirmation}")
    print(f"approx_avg_json_chars: {avg_len}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
