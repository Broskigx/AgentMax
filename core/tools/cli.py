"""Command line interface for AgentMax tools diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from core.tools.testing_runner import ToolTestingRunner


async def _run(args: argparse.Namespace) -> int:
    runner = ToolTestingRunner()
    if args.command == "validate-json":
        report = await runner.validate_json()
    elif args.command == "doctor":
        report = await runner.doctor()
    elif args.command == "test":
        category = "mouse" if args.mouse else None
        report = await runner.run(category=category, safe=args.safe, integration=args.integration)
    else:
        raise SystemExit(2)

    payload = runner.to_dict(report)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="AgentMax tools")
    sub = parser.add_subparsers(dest="command", required=True)

    test = sub.add_parser("test", help="Run dry-run tool tests")
    test.add_argument("--mouse", action="store_true", help="Run only mouse tools")
    test.add_argument("--safe", action="store_true", default=True, help="Use safe sandbox mode")
    test.add_argument("--integration", action="store_true", help="Execute real integration tests")

    sub.add_parser("validate-json", help="Validate core/ai/tools.json")
    sub.add_parser("doctor", help="Run tool system diagnostics")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))
