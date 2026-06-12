#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "datasets" / "AgentMax_v2"
DEFAULT_INPUT = DATASET_DIR / "AgentMax_sft_v2.jsonl"
SEED = 42


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def split_group(rows: list[dict], rng: random.Random) -> tuple[list[dict], list[dict], list[dict]]:
    shuffled = rows[:]
    rng.shuffle(shuffled)
    n = len(shuffled)
    train_n = int(n * 0.85)
    val_n = int(n * 0.10)
    test_n = n - train_n - val_n
    if n >= 3 and test_n == 0:
        test_n = 1
        train_n = max(1, train_n - 1)
    train = shuffled[:train_n]
    validation = shuffled[train_n : train_n + val_n]
    test = shuffled[train_n + val_n :]
    return train, validation, test


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    rows = read_jsonl(args.input)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("category", "missing_category"))].append(row)

    rng = random.Random(args.seed)
    train: list[dict] = []
    validation: list[dict] = []
    test: list[dict] = []

    for category in sorted(grouped):
        group_train, group_validation, group_test = split_group(grouped[category], rng)
        train.extend(group_train)
        validation.extend(group_validation)
        test.extend(group_test)

    rng.shuffle(train)
    rng.shuffle(validation)
    rng.shuffle(test)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "train.jsonl", train)
    write_jsonl(args.out_dir / "validation.jsonl", validation)
    write_jsonl(args.out_dir / "test.jsonl", test)

    print(f"Input: {len(rows)}")
    print(f"Train: {len(train)}")
    print(f"Validation: {len(validation)}")
    print(f"Test: {len(test)}")
    print(f"Seed: {args.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
