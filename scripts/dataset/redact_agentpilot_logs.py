#!/usr/bin/env python3
"""Redact raw AgentMax session logs into data/AgentMax_logs/redacted."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.data_collection.redactor import redact_record

RAW_DIR = ROOT / "data" / "AgentMax_logs" / "raw"
REDACTED_DIR = ROOT / "data" / "AgentMax_logs" / "redacted"


def redact_file(src: Path, dst: Path) -> tuple[int, int]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    written = 0
    with (
        src.open("r", encoding="utf-8") as inp,
        dst.open("w", encoding="utf-8", newline="\n") as out,
    ):
        for line in inp:
            if not line.strip():
                continue
            total += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                record = {"parse_error": True, "raw": line.strip()}
            out.write(
                json.dumps(redact_record(record), ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            written += 1
    return total, written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=RAW_DIR)
    parser.add_argument("--output", type=Path, default=REDACTED_DIR)
    args = parser.parse_args()

    files = [args.input] if args.input.is_file() else sorted(args.input.glob("*.jsonl"))
    total = 0
    written = 0
    for src in files:
        dst = (
            args.output / src.name
            if args.output.is_dir() or not args.output.suffix
            else args.output
        )
        t, w = redact_file(src, dst)
        total += t
        written += w
        print(f"redacted {src} -> {dst} ({w}/{t})")
    print(f"total={total} written={written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
