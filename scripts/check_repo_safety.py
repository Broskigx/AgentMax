#!/usr/bin/env python3
"""Fail CI on newly tracked release hazards; report historical generated debt."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 95 * 1024 * 1024
MODEL_EXTENSIONS = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
}
GENERATED_EXTENSIONS = {".db", ".dll", ".exe", ".msi", ".sqlite", ".zip"}
SAFE_ENV_EXAMPLES = {".env.example"}
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
)
SAFE_FIXTURE_MARKERS = ("dummy", "example", "placeholder", "test-token", "<secret>")


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        ROOT / value.decode("utf-8", errors="surrogateescape")
        for value in result.stdout.split(b"\0")
        if value
    ]


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        lowered = path.name.lower()
        suffix = path.suffix.lower()
        if suffix in MODEL_EXTENSIONS:
            errors.append(f"tracked model/weight: {relative}")
        if (lowered == ".env" or lowered.startswith(".env.")) and lowered not in SAFE_ENV_EXAMPLES:
            errors.append(f"tracked environment file: {relative}")
        if path.exists() and path.stat().st_size > MAX_BYTES:
            errors.append(
                f"tracked file exceeds 95 MiB: {relative} ({path.stat().st_size} bytes)"
            )
        if suffix in GENERATED_EXTENSIONS or relative.startswith("diagnostics/"):
            warnings.append(f"tracked generated artifact: {relative}")
        if not path.exists() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                nearby = text[max(0, match.start() - 100) : match.end() + 100].lower()
                if any(marker in nearby for marker in SAFE_FIXTURE_MARKERS):
                    continue
                errors.append(f"possible tracked secret in {relative}: {pattern.pattern}")
                break
            else:
                continue
            break

    for warning in sorted(set(warnings)):
        print(f"WARNING: {warning}")
    for error in sorted(set(errors)):
        print(f"ERROR: {error}", file=sys.stderr)
    print(
        f"repo safety: {len(errors)} error(s), {len(set(warnings))} warning(s)",
        file=sys.stderr if errors else sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
