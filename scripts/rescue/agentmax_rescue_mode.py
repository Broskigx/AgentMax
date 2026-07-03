#!/usr/bin/env python3
"""AgentMax Rescue Mode.

External diagnostic helper for cases where the desktop UI does not start.  It
collects read-only diagnostics and exports a zip package.  It never collects
secrets intentionally and does not execute destructive commands.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.data_collection.redactor import redact_record
from core.tools.safety_supervisor import AgentToolSupervisor

OUT_DIR = ROOT / "data" / "AGENTMAX_rescue"


READ_ONLY_COMMANDS = {
    "whoami": ["whoami"],
    "systeminfo": ["cmd", "/c", "systeminfo"]
    if platform.system() == "Windows"
    else ["uname", "-a"],
    "processes": ["cmd", "/c", "tasklist"] if platform.system() == "Windows" else ["ps", "aux"],
    "events_application": ["cmd", "/c", "wevtutil", "qe", "Application", "/c:20", "/f:text"]
    if platform.system() == "Windows"
    else ["sh", "-lc", "journalctl -n 20 --no-pager 2>/dev/null || true"],
}


def run_read_only(name: str, args: list[str]) -> dict[str, Any]:
    supervisor = AgentToolSupervisor()
    command_text = " ".join(args)
    decision = supervisor.inspect_command(command_text)
    if not decision.allowed and decision.blocked:
        return {"name": name, "skipped": True, "reason": decision.reason}
    started = time.time()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return {
            "name": name,
            "command": command_text,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-20_000:],
            "stderr": proc.stderr[-8_000:],
            "duration_ms": round((time.time() - started) * 1000, 2),
        }
    except Exception as exc:
        return {"name": name, "command": command_text, "error": str(exc)}


def collect(output_dir: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    package_dir = output_dir / f"rescue-{stamp}"
    package_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "repo": str(ROOT),
        "commands": [
            redact_record(run_read_only(name, cmd)) for name, cmd in READ_ONLY_COMMANDS.items()
        ],
        "log_files": [],
    }

    for folder in [
        ROOT / "logs",
        ROOT / "runtime_logs",
        ROOT / "data" / "AgentMax_logs" / "redacted",
    ]:
        if folder.exists():
            for path in sorted(folder.glob("*.log"))[:20] + sorted(folder.glob("*.jsonl"))[:20]:
                report["log_files"].append(str(path))

    report_path = package_dir / "rescue_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    instructions = package_dir / "README_RESCUE.txt"
    instructions.write_text(
        "AgentMax Rescue Mode\n"
        "This package contains read-only diagnostics. Review it before sharing.\n"
        "No screenshot is captured by this helper unless you attach one manually.\n",
        encoding="utf-8",
    )

    zip_path = output_dir / f"AgentMax-rescue-{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in package_dir.rglob("*"):
            zf.write(path, path.relative_to(package_dir))
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    zip_path = collect(args.output_dir)
    print(f"rescue package: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
