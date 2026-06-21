"""Load eval cases from a JSONL file.

Each line is a JSON object with an ``input`` and optional ``expected``, ``id``
and ``metadata`` fields.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.evals.types import EvalCase


class DatasetError(ValueError):
    """Raised when an eval dataset cannot be parsed."""


def load_cases(path: str | Path) -> list[EvalCase]:
    """Load eval cases from a JSONL file (blank lines ignored)."""
    cases: list[EvalCase] = []
    text = Path(path).read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"Invalid JSON on line {lineno}: {exc}") from exc
        if "input" not in obj:
            raise DatasetError(f"Missing 'input' on line {lineno}")
        cases.append(
            EvalCase(
                input=str(obj["input"]),
                expected=str(obj.get("expected", "")),
                id=str(obj.get("id") or f"case-{lineno}"),
                metadata=dict(obj.get("metadata") or {}),
            )
        )
    return cases


__all__ = ["load_cases", "DatasetError"]
