#!/usr/bin/env python3
"""AgentMax closed beta maintenance CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.beta import StorageService, export_diagnostics_bundle
from core.beta.smoke import run_beta_smoke_test


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentMax closed beta tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset-db", help="Reset the local beta SQLite database")
    export_feedback = sub.add_parser(
        "export-feedback", help="Export tester feedback to JSON and CSV"
    )
    export_feedback.add_argument("--output-dir", type=Path, default=ROOT / "beta_exports")
    export_diag = sub.add_parser("export-diagnostics", help="Export redacted diagnostics bundle")
    export_diag.add_argument("--output-dir", type=Path, default=ROOT / "diagnostics")
    smoke = sub.add_parser("smoke-test", help="Run the closed beta smoke test")
    smoke.add_argument("--output-dir", type=Path, default=ROOT / "diagnostics" / "smoke")
    args = parser.parse_args()

    storage = StorageService()
    if args.command == "reset-db":
        storage.reset_dev()
        print(json.dumps({"ok": True, "sqlite": storage.status()}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "export-feedback":
        print(json.dumps(storage.export_feedback(args.output_dir), ensure_ascii=False, indent=2))
        return 0
    if args.command == "export-diagnostics":
        print(
            json.dumps(
                export_diagnostics_bundle(args.output_dir, storage=storage),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "smoke-test":
        result = run_beta_smoke_test(args.output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
