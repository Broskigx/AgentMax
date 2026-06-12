"""
Verifica que el dataset este OK antes de gastar GPU.
Corrida local en tu PC con Python (no necesita GPU ni unsloth).

Uso: python scripts/verify_dataset.py
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRAIN = os.path.join(ROOT, "data", "train.jsonl")
VALID = os.path.join(ROOT, "data", "valid.jsonl")


def check_file(path: str, label: str):
    if not os.path.exists(path):
        print(f"[ERROR] {label} no existe: {path}")
        return False
    n_lines = 0
    role_counts: Counter = Counter()
    tool_calls_count = 0
    empty_content = 0
    long_msgs = 0
    parse_errors = 0
    total_chars = 0
    max_chars = 0

    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            n_lines += 1
            try:
                ex = json.loads(line)
            except Exception as e:
                parse_errors += 1
                if parse_errors < 3:
                    print(f"  [WARN] linea {i+1}: parse error: {e}")
                continue
            msgs = ex.get("messages", [])
            if not msgs:
                empty_content += 1
                continue
            sample_chars = 0
            for m in msgs:
                role = m.get("role", "?")
                role_counts[role] += 1
                content = m.get("content", "") or ""
                sample_chars += len(content)
                if not content and role != "tool" and not m.get("tool_calls"):
                    empty_content += 1
                if m.get("tool_calls"):
                    tool_calls_count += 1
            total_chars += sample_chars
            if sample_chars > max_chars:
                max_chars = sample_chars
            if sample_chars > 12000:  # ~3000 tokens
                long_msgs += 1

    print(f"\n[{label}] {path}")
    print(f"  Lineas:           {n_lines}")
    print(f"  Errores parse:    {parse_errors}")
    print(f"  Roles:            {dict(role_counts)}")
    print(f"  Assistant w/tool: {tool_calls_count}")
    print(f"  Mensajes vacios:  {empty_content}")
    print(f"  Samples >3k tok:  {long_msgs}")
    if n_lines:
        avg_chars = total_chars / n_lines
        print(f"  Chars/sample avg: {avg_chars:.0f} (~{avg_chars/4:.0f} tokens)")
        print(f"  Chars/sample max: {max_chars} (~{max_chars/4:.0f} tokens)")

    ok = parse_errors == 0 and n_lines > 0
    print(f"  Status: {'OK' if ok else 'FAIL'}")
    return ok


def main():
    print("=" * 60)
    print("   AgentMax - Dataset Verifier")
    print("=" * 60)

    ok_train = check_file(TRAIN, "TRAIN")
    ok_valid = check_file(VALID, "VALID")

    print("\n" + "=" * 60)
    if ok_train and ok_valid:
        print("   [OK] Dataset listo para entrenar")
        sys.exit(0)
    else:
        print("   [FAIL] Hay errores - revisar arriba")
        sys.exit(1)


if __name__ == "__main__":
    main()
