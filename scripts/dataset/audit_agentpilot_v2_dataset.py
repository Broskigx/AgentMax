#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "datasets" / "AgentMax_v2"
DEFAULT_FILES = [
    DATASET_DIR / "AgentMax_sft_v2.jsonl",
    DATASET_DIR / "AgentMax_eval_v2.jsonl",
]
REJECTED_PATH = DATASET_DIR / "AgentMax_rejected_patterns.txt"

COMMON_FILES = [
    "config.json",
    "error.log",
    "package.json",
    ".env",
    "docker-compose.yml",
    "server.js",
    "app.py",
    "requirements.txt",
    "pyproject.toml",
]

DESTRUCTIVE_COMMANDS = [
    r"\brm\s+-rf\b",
    r"\bdel\s+/s\b",
    r"\bformat\s+[a-z]:",
    r"\bkill\s+-9\b",
    r"\bshutdown\s+/",
    r"\bRemove-Item\b.*\b-Recurse\b.*\b-Force\b",
    r"\btaskkill\b.*\b/F\b",
]

UNVERIFIED_ACTIONS = [
    r"\bejecut[eé]\b",
    r"\ble[ií]\b",
    r"\babr[ií]\b",
    r"\brevis[eé]\b",
    r"\bmodifiqu[eé]\b",
    r"\bcambi[eé]\b",
    r"\bvi\b",
]


def load_rejected() -> list[str]:
    if not REJECTED_PATH.exists():
        return []
    return [
        line.strip()
        for line in REJECTED_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sentence_count(text: str) -> int:
    parts = [p.strip() for p in re.split(r"[.!?]+(?:\s|$)", text) if p.strip()]
    return len(parts)


def messages_by_role(row: dict[str, Any]) -> dict[str, str]:
    messages = row.get("messages")
    if not isinstance(messages, list):
        return {}
    return {
        str(msg.get("role")): str(msg.get("content", ""))
        for msg in messages
        if isinstance(msg, dict)
    }


def has_evidence(user: str) -> bool:
    lowered = user.lower()
    evidence_terms = [
        "tool_result",
        "resultado de herramienta",
        "salida:",
        "log:",
        "stack trace",
        "captura",
        "revisa este error",
        "este error",
        "este código",
        "```",
    ]
    return any(term in lowered for term in evidence_terms)


def audit_file(path: Path, rejected: list[str]) -> tuple[list[str], Counter[str], int]:
    errors: list[str] = []
    categories: Counter[str] = Counter()
    exact_seen: dict[str, int] = {}
    count = 0

    if not path.exists():
        return [f"{path}: file not found"], categories, count

    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            raw = line.strip()
            if not raw:
                continue
            count += 1
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"{path}:{line_no}: invalid JSON: {exc}")
                continue

            exact_key = json.dumps(row.get("messages", row), sort_keys=True, ensure_ascii=False)
            if exact_key in exact_seen:
                errors.append(
                    f"{path}:{line_no}: duplicate exact example, first at line {exact_seen[exact_key]}"
                )
            else:
                exact_seen[exact_key] = line_no

            category = str(row.get("category", "missing_category"))
            categories[category] += 1
            roles = messages_by_role(row)
            for role in ("system", "user", "assistant"):
                if role not in roles or not roles[role].strip():
                    errors.append(f"{path}:{line_no}: missing {role} message")
            if len(row.get("messages", [])) != 3:
                errors.append(f"{path}:{line_no}: expected exactly 3 messages")
                continue

            user = roles.get("user", "")
            assistant = roles.get("assistant", "")
            assistant_lower = assistant.lower()
            user_lower = user.lower()

            for pattern in rejected:
                if pattern.lower() in assistant_lower:
                    errors.append(f"{path}:{line_no}: rejected pattern in assistant: {pattern}")

            sc = sentence_count(assistant)
            if sc < 2 or sc > 8:
                errors.append(f"{path}:{line_no}: assistant sentence count {sc}, expected 2..8")

            for filename in COMMON_FILES:
                if filename.lower() in assistant_lower and filename.lower() not in user_lower:
                    positions = [
                        m.start() for m in re.finditer(re.escape(filename.lower()), assistant_lower)
                    ]
                    allowed = False
                    for pos in positions:
                        window = assistant_lower[max(0, pos - 60) : pos + len(filename) + 20]
                        if any(
                            marker in window
                            for marker in (
                                "si existe",
                                "si tienes",
                                "en caso de que exista",
                                "cuando exista",
                            )
                        ):
                            allowed = True
                    if not allowed:
                        errors.append(
                            f"{path}:{line_no}: possible invented file mention: {filename}"
                        )

            for pattern in DESTRUCTIVE_COMMANDS:
                if re.search(pattern, assistant, flags=re.IGNORECASE | re.DOTALL):
                    errors.append(f"{path}:{line_no}: destructive command pattern: {pattern}")

            if not has_evidence(user):
                for pattern in UNVERIFIED_ACTIONS:
                    if re.search(pattern, assistant_lower):
                        errors.append(
                            f"{path}:{line_no}: unverifiable past action in assistant: {pattern}"
                        )

    return errors, categories, count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", type=Path, default=DEFAULT_FILES)
    args = parser.parse_args()

    rejected = load_rejected()
    all_errors: list[str] = []
    total = 0
    all_categories: Counter[str] = Counter()

    for path in args.files:
        errors, categories, count = audit_file(path, rejected)
        total += count
        all_categories.update(categories)
        all_errors.extend(errors)

    print(f"Total examples audited: {total}")
    print("Examples by category:")
    for category, count in sorted(all_categories.items()):
        print(f"  {category}: {count}")

    if all_errors:
        print("\nCritical errors:")
        for err in all_errors[:200]:
            print(f"  - {err}")
        if len(all_errors) > 200:
            print(f"  ... {len(all_errors) - 200} more")
        return 1

    print("\nAudit passed: no critical errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
