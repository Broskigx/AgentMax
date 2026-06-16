"""Top-level AgentMax command line interface."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "doctor":
        from core.tools.cli import main as tools_main

        return tools_main(["doctor", *args[1:]])
    if args and args[0] == "tools":
        from core.tools.cli import main as tools_main

        return tools_main(args[1:])
    if args and args[0] == "serve":
        import runpy
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        script = root / "scripts" / "agentmax_server.py"
        if not script.is_file():
            print(f"Missing backend script: {script}", file=sys.stderr)
            return 1
        sys.argv = [str(script), *args[1:]]
        runpy.run_path(str(script), run_name="__main__")
        return 0
    print("Usage: python -m core.cli <serve|doctor|tools> [options]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
