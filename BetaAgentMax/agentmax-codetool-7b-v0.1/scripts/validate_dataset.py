from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = [ROOT / "data" / "train.jsonl", ROOT / "data" / "valid.jsonl"]
TOOLS_SCHEMA = ROOT / "configs" / "tools_schema.json"
ALLOWED_ROLES = {"system", "user", "assistant", "tool"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def load_tool_names() -> set[str]:
    try:
        schema = json.loads(TOOLS_SCHEMA.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR tools_schema.json: {exc}")
        sys.exit(1)
    tools = schema.get("tools")
    if not isinstance(tools, list):
        print("ERROR tools_schema.json: `tools` must be a list")
        sys.exit(1)
    names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
    if not all(isinstance(name, str) and name for name in names):
        print("ERROR tools_schema.json: every tool needs a non-empty name")
        sys.exit(1)
    return set(names)  # type: ignore[arg-type]


def contains_secret(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def validate_message(message: Any, path: Path, line_no: int, idx: int, tool_names: set[str]) -> list[str]:
    errors: list[str] = []
    prefix = f"{path.name}:{line_no}:messages[{idx}]"
    if not isinstance(message, dict):
        return [f"{prefix}: message must be an object"]
    role = message.get("role")
    if role not in ALLOWED_ROLES:
        errors.append(f"{prefix}: invalid role {role!r}")
    if "content" not in message:
        errors.append(f"{prefix}: missing content")
    if role == "assistant" and "tool_calls" in message:
        calls = message["tool_calls"]
        if not isinstance(calls, list):
            errors.append(f"{prefix}: tool_calls must be a list")
        else:
            for call_idx, call in enumerate(calls):
                cprefix = f"{prefix}.tool_calls[{call_idx}]"
                if not isinstance(call, dict):
                    errors.append(f"{cprefix}: tool_call must be an object")
                    continue
                name = call.get("name")
                args = call.get("arguments")
                if not isinstance(name, str) or not name:
                    errors.append(f"{cprefix}: missing or invalid name")
                elif name not in tool_names:
                    errors.append(f"{cprefix}: unknown tool name {name!r}")
                if not isinstance(args, dict):
                    errors.append(f"{cprefix}: arguments must be an object")
    return errors


def validate_file(path: Path, tool_names: set[str]) -> tuple[int, list[str]]:
    errors: list[str] = []
    count = 0
    if not path.exists():
        return 0, [f"{path}: file does not exist"]
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                errors.append(f"{path.name}:{line_no}: blank line")
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name}:{line_no}: invalid JSON: {exc}")
                continue
            count += 1
            if not isinstance(row, dict):
                errors.append(f"{path.name}:{line_no}: row must be an object")
                continue
            messages = row.get("messages")
            if not isinstance(messages, list) or not messages:
                errors.append(f"{path.name}:{line_no}: messages must be a non-empty list")
                continue
            if contains_secret(row):
                errors.append(f"{path.name}:{line_no}: possible secret pattern detected")
            for idx, message in enumerate(messages):
                errors.extend(validate_message(message, path, line_no, idx, tool_names))
    return count, errors


def main() -> int:
    tool_names = load_tool_names()
    total = 0
    all_errors: list[str] = []
    for path in DATA_FILES:
        count, errors = validate_file(path, tool_names)
        total += count
        print(f"{path.relative_to(ROOT)}: {count} examples")
        all_errors.extend(errors)
    if all_errors:
        print("\nValidation errors:")
        for error in all_errors:
            print(f"- {error}")
        return 1
    print(f"OK: {total} examples validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
