#!/usr/bin/env python3
"""
Nuitka build for AgentMax — compiles the AgentMax CLI to a native binary.

Why Nuitka (not PyInstaller): Nuitka compiles Python to C and then to a native
executable, so the shipped binary contains **no readable Python bytecode** — far
harder to reverse-engineer than a PyInstaller archive (which is trivially
unpacked). NOTE: no shipped binary is ever truly "impossible" to analyse; this
raises the bar substantially, and is combined with the runtime anti-tamper layer.

Heavy ML deps (torch/transformers/peft/unsloth) are intentionally **excluded**:
the local AgentMax model is loaded lazily from disk only when that backend is
selected, so the shipped binary stays small. Testers use the cloud (Claude) or
LM Studio backend out of the box; AgentMax-local is an optional add-on.

Usage:
    python scripts/build_exe.py                 # standalone folder (fast, testable)
    python scripts/build_exe.py --onefile       # single distributable binary
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "AgentMax.py"
VERSION = "0.2.0"

# ML / training stacks + dev-only tooling that must never be compiled into the
# shipped binary (they are loaded lazily/never at runtime and only add bloat).
EXCLUDE_MODULES = [
    # Heavy ML / training (AgentMax-local loads these lazily from disk)
    "torch",
    "torchvision",
    "transformers",
    "peft",
    "unsloth",
    "accelerate",
    "bitsandbytes",
    "datasets",
    "safetensors",
    "tensorboard",
    # Dev / build-only tooling pulled in by the import graph but never run at runtime
    "mypy",
    "pytest",
    "_pytest",
    "ruff",
    "nuitka",
    "IPython",
    "setuptools",
]

# Packages whose submodules are resolved lazily/dynamically at runtime, which
# Nuitka's static analysis misses (e.g. websockets.asyncio.server, uvicorn's
# protocol/loop autodetection). Force every submodule in so the IPC servers work.
INCLUDE_PACKAGES = ["core", "sdk", "websockets", "uvicorn", "h11", "anthropic"]

# Runtime data the app reads from disk; only included if present.
DATA_FILES = [("core/ai/tools.json", "core/ai/tools.json")]
DATA_DIRS = [("core/i18n/locales", "core/i18n/locales")]


def build(onefile: bool, output_dir: Path) -> int:
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",  # auto-fetch MinGW64 if no C compiler present
        f"--output-dir={output_dir}",
        "--output-filename=AgentMax",
        "--company-name=AgentMax",
        "--product-name=AgentMax",
        f"--file-version={VERSION}.0",
        f"--product-version={VERSION}",
        "--file-description=AgentMax - AI Desktop Operating Agent",
        "--copyright=Copyright (c) 2026 AgentMax. All rights reserved.",
        "--remove-output",
    ]
    for pkg in INCLUDE_PACKAGES:
        cmd.append(f"--include-package={pkg}")
    if onefile:
        cmd.append("--onefile")
    for mod in EXCLUDE_MODULES:
        cmd.append(f"--nofollow-import-to={mod}")
    for src, dst in DATA_FILES:
        if (ROOT / src).exists():
            cmd.append(f"--include-data-files={src}={dst}")
    for src, dst in DATA_DIRS:
        if (ROOT / src).is_dir():
            cmd.append(f"--include-data-dir={src}={dst}")
    if sys.platform == "win32":
        cmd.append("--windows-console-mode=force")
        icon = ROOT / "assets" / "icon.ico"
        if icon.exists():
            cmd.append(f"--windows-icon-from-ico={icon}")
    cmd.append(str(ENTRY))

    print("Building AgentMax with Nuitka...\n  " + " ".join(cmd) + "\n")
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the AgentMax native binary with Nuitka.")
    ap.add_argument("--onefile", action="store_true", help="produce a single distributable binary")
    ap.add_argument("--output-dir", default=str(ROOT / "dist" / "nuitka"))
    args = ap.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rc = build(args.onefile, out)
    print("\nBuild OK." if rc == 0 else f"\nBuild FAILED (rc={rc}).")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
