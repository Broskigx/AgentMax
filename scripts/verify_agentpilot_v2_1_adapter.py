#!/usr/bin/env python3
"""
Verify the AgentMax V2.1 LoRA adapter is correctly installed.

WHAT THIS SCRIPT DOES
---------------------
- Walks the local adapter directory.
- Confirms every required file exists with non-zero size.
- Loads adapter_config.json and prints the key LoRA fields.
- Prints the manifest summary.
- Confirms the backup archive is in place.
- Re-checks (optionally) the sha256 of adapter_model.safetensors against the manifest.

WHAT THIS SCRIPT DOES NOT DO
----------------------------
- Does NOT download any model.
- Does NOT run inference.
- Does NOT load the base model.
- Does NOT touch the original tar.gz.

Exit codes
----------
0   adapter looks ready
1   missing required files
2   manifest missing / malformed
3   adapter_config.json invalid
4   sha256 mismatch (only when --check-hash is passed)

Usage
-----
    python scripts/verify_AgentMax_v2_1_adapter.py
    python scripts/verify_AgentMax_v2_1_adapter.py --check-hash
    python scripts/verify_AgentMax_v2_1_adapter.py --adapter <path>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V21 = DEFAULT_ROOT / "models" / "AgentMax" / "V2.1"
DEFAULT_ADAPTER = DEFAULT_V21 / "adapter"
DEFAULT_MANIFEST = DEFAULT_V21 / "AgentMax_v2_1_manifest.json"

REQUIRED_FILES = (
    "adapter_config.json",
    "adapter_model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "processor_config.json",
    "chat_template.jinja",
)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n = n / 1024
    return f"{n:.1f} TB"


def _print_section(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--adapter",
        type=Path,
        default=DEFAULT_ADAPTER,
        help=f"Adapter directory (default: {DEFAULT_ADAPTER})",
    )
    p.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Manifest JSON (default: {DEFAULT_MANIFEST})",
    )
    p.add_argument(
        "--check-hash",
        action="store_true",
        help="Compute sha256 of adapter_model.safetensors and compare to manifest",
    )
    args = p.parse_args()

    adapter: Path = args.adapter
    manifest_path: Path = args.manifest

    _print_section("AgentMax V2.1 Adapter Verification")
    print(f"  adapter:   {adapter}")
    print(f"  manifest:  {manifest_path}")

    # 1) Adapter directory exists ------------------------------------------------
    if not adapter.exists():
        print("  [FAIL] adapter directory not found")
        return 1
    if not adapter.is_dir():
        print("  [FAIL] adapter path is not a directory")
        return 1
    print("  adapter directory: OK")

    # 2) Required files ----------------------------------------------------------
    _print_section("Required files")
    missing: list[str] = []
    sizes: dict[str, int] = {}
    for fname in REQUIRED_FILES:
        fpath = adapter / fname
        if not fpath.exists() or fpath.stat().st_size == 0:
            print(f"  [MISSING]  {fname}")
            missing.append(fname)
        else:
            sz = fpath.stat().st_size
            sizes[fname] = sz
            print(f"  [OK]  {sz:>14,} bytes ({_human_size(sz):>9})  {fname}")
    if missing:
        print()
        print(f"  [FAIL] {len(missing)} required file(s) missing:")
        for m in missing:
            print(f"    - {m}")
        return 1

    # 3) adapter_config.json shape ------------------------------------------------
    _print_section("adapter_config.json")
    try:
        cfg = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  [FAIL] cannot parse adapter_config.json: {exc}")
        return 3
    peft_type = cfg.get("peft_type", "?")
    base = cfg.get("base_model_name_or_path", "?")
    rank = cfg.get("r", "?")
    alpha = cfg.get("lora_alpha", "?")
    task = cfg.get("task_type", "?")
    print(f"  peft_type:    {peft_type}")
    print(f"  base_model:   {base}")
    print(f"  rank (r):     {rank}")
    print(f"  alpha:        {alpha}")
    print(f"  task_type:    {task}")
    if peft_type != "LORA":
        print(f"  [FAIL] expected peft_type=LORA, got {peft_type!r}")
        return 3

    # 4) Manifest -----------------------------------------------------------------
    _print_section("Manifest")
    if not manifest_path.exists():
        print(f"  [FAIL] manifest not found: {manifest_path}")
        return 2
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  [FAIL] cannot parse manifest: {exc}")
        return 2
    for k in ("name", "type", "base_model", "adapter_path", "status"):
        print(f"  {k:18}  {manifest.get(k)}")
    notes = manifest.get("notes")
    if notes:
        print(f"  notes:              {notes[:200]}")

    # 5) Backup archive exists ----------------------------------------------------
    backup = manifest.get("backup_archive")
    if backup:
        bpath = Path(backup)
        if bpath.exists():
            sz = bpath.stat().st_size
            print(f"  backup archive:     OK  ({sz:,} bytes)  {bpath}")
        else:
            print(f"  [WARN] backup archive declared in manifest but not found: {bpath}")

    # 6) Optional sha256 check ----------------------------------------------------
    if args.check_hash:
        _print_section("sha256 integrity (slow)")
        sf = adapter / "adapter_model.safetensors"
        manifest_hash = manifest.get("files", {}).get("adapter_model.safetensors", {}).get("sha256")
        if not manifest_hash:
            print("  [SKIP] manifest does not record a sha256")
        else:
            print(f"  computing sha256 of {sf.stat().st_size:,} bytes...")
            actual = _sha256_of(sf)
            ok = actual == manifest_hash
            print(f"  expected: {manifest_hash}")
            print(f"  actual:   {actual}")
            print(f"  {'OK' if ok else 'MISMATCH'}")
            if not ok:
                return 4

    # 7) Final OK -----------------------------------------------------------------
    _print_section("Result")
    print("  status: READY")
    print(f"  All {len(REQUIRED_FILES)} required files present.")
    print(
        f"  Total adapter weight (safetensors): {_human_size(sizes['adapter_model.safetensors'])}"
    )
    print()
    print("  Next step: AgentMax loads this adapter on top of the base model")
    print(f"             ({manifest.get('base_model')}). Do NOT load this adapter")
    print("             standalone; it is not a full model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
