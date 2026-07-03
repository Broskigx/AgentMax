"""Diagnostics export for AgentMax closed beta."""

from __future__ import annotations

import json
import platform
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.data_collection.redactor import redact_record, redact_text

from .config import ROOT, BetaConfig, get_beta_config
from .storage import StorageService

_FORBIDDEN_NAMES = {
    ".env",
    "cookies",
    "login data",
    "web data",
    "local state",
    "credentials",
    "secrets",
}
_FORBIDDEN_EXTENSIONS = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
}


def _copy_redacted_logs(package_dir: Path, config: BetaConfig) -> list[str]:
    out_dir = package_dir / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for folder in (
        config.data_dir / "logs",
        ROOT / "logs" / "agentmax",
        ROOT / "logs",
        ROOT / "runtime_logs",
    ):
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.log"))[:40]:
            target = out_dir / f"{folder.name}-{path.name}"
            text = redact_text(path.read_text(encoding="utf-8", errors="replace")[-80_000:])
            target.write_text(text, encoding="utf-8")
            copied.append(str(target.relative_to(package_dir)))
    return copied


def export_diagnostics_bundle(
    output_dir: str | Path | None = None,
    *,
    config: BetaConfig | None = None,
    storage: StorageService | None = None,
) -> dict[str, Any]:
    cfg = config or get_beta_config()
    store = storage or StorageService(config=cfg)
    store.migrate()

    base = Path(output_dir) if output_dir else ROOT / "diagnostics"
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    package_dir = base / f"agentmax-diagnostics-{stamp}"
    package_dir.mkdir(parents=True, exist_ok=True)

    feedback_paths = store.export_feedback(package_dir)
    copied_logs = _copy_redacted_logs(package_dir, cfg)
    report = redact_record(
        {
            "created_at": datetime.now(UTC).isoformat(),
            "app": {"name": cfg.app_name, "version": cfg.app_version, "build": cfg.build_number},
            "environment": cfg.app_env,
            "beta_mode": cfg.beta_mode,
            "config": cfg.safe_dict,
            "sqlite": store.status(),
            "latest_errors": store.latest_errors(20),
            "feedback_exports": feedback_paths,
            "logs": copied_logs,
            "system": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "machine": platform.machine(),
            },
        }
    )
    (package_dir / "diagnostics_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (package_dir / "README.txt").write_text(
        "AgentMax diagnostics bundle. Review before sharing. Secrets are redacted automatically.\n",
        encoding="utf-8",
    )
    _assert_safe_bundle(package_dir)

    zip_path = base / f"agentmax-diagnostics-{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in package_dir.rglob("*"):
            zf.write(path, path.relative_to(package_dir))
    return {
        "ok": True,
        "zip_path": str(zip_path),
        "folder": str(package_dir),
        "created_at": time.time(),
    }


def _assert_safe_bundle(package_dir: Path) -> None:
    allowed_roots = {"README.txt", "diagnostics_report.json", "logs"}
    for path in package_dir.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(package_dir)
        if relative.parts[0] not in allowed_roots and not relative.name.startswith(
            "agentmax-feedback-"
        ):
            raise ValueError(f"Unexpected diagnostics artifact: {relative}")
        lowered = path.name.lower()
        if (
            lowered in _FORBIDDEN_NAMES
            or path.suffix.lower() in _FORBIDDEN_EXTENSIONS
            or any(name in lowered for name in ("private_key", "api_key", "token_dump"))
        ):
            raise ValueError(f"Forbidden diagnostics artifact: {relative}")
