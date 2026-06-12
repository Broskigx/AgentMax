#!/usr/bin/env python3
"""Audit AgentMax logs before they become fine-tuning examples."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APPROVED_DIR = ROOT / "data" / "AgentMax_logs" / "approved"

FORBIDDEN = [
    re.compile(r"<\s*/?\s*think\s*>", re.IGNORECASE),
    re.compile(r"(?i)hago la acción del momento"),
    re.compile(r"(?i)no soy chatgpt ni gemini"),
    re.compile(r"(?i)cifro su código"),
    re.compile(r"(?i)borro todo"),
]
SECRET_MARKERS = [
    re.compile(r"(?i)(api[_-]?key|password|token|secret)\s*[:=]\s*[^\s,]{6,}"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
]


def assistant_text(record: dict[str, Any]) -> str:
    return str(
        record.get("sanitized_response")
        or record.get("assistant_response")
        or record.get("assistant")
        or ""
    )


def audit_file(path: Path) -> tuple[list[str], Counter[str], int]:
    errors: list[str] = []
    stats: Counter[str] = Counter()
    count = 0
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            count += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path}:{line_no}: invalid JSON: {exc}")
                continue

            status = str(record.get("outcome") or record.get("status") or "unknown")
            stats[status] += 1
            text = assistant_text(record)
            if not text.strip():
                errors.append(f"{path}:{line_no}: missing assistant/sanitized response")
            for pattern in FORBIDDEN:
                if pattern.search(text):
                    errors.append(
                        f"{path}:{line_no}: forbidden assistant pattern: {pattern.pattern}"
                    )
            raw = json.dumps(record, ensure_ascii=False)
            for pattern in SECRET_MARKERS:
                if pattern.search(raw):
                    errors.append(f"{path}:{line_no}: possible unredacted secret or email")
    return errors, stats, count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path, default=[APPROVED_DIR])
    args = parser.parse_args()

    files: list[Path] = []
    for path in args.paths:
        files.extend([path] if path.is_file() else sorted(path.glob("*.jsonl")))

    all_errors: list[str] = []
    total = 0
    stats: Counter[str] = Counter()
    for path in files:
        errors, file_stats, count = audit_file(path)
        all_errors.extend(errors)
        stats.update(file_stats)
        total += count

    print(f"logs={total}")
    for key, value in sorted(stats.items()):
        print(f"{key}: {value}")
    if all_errors:
        print("critical_errors:")
        for error in all_errors[:100]:
            print(f"- {error}")
        if len(all_errors) > 100:
            print(f"... {len(all_errors) - 100} more")
        return 1
    print("audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
