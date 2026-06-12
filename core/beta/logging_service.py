"""Structured redacted logs for the closed beta."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.data_collection.redactor import redact_record

from .config import ROOT
from .storage import StorageService

LOG_NAMES = {
    "app": "app.log",
    "agent": "agent.log",
    "tools": "tools.log",
    "errors": "errors.log",
    "beta_feedback": "beta_feedback.log",
}


class BetaLogger:
    def __init__(self, log_dir: str | Path | None = None, storage: StorageService | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir else ROOT / "logs" / "agentmax"
        self.storage = storage or StorageService()

    def log(
        self,
        log_name: str,
        *,
        level: str,
        source: str,
        event: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        file_name = LOG_NAMES.get(log_name, f"{log_name}.log")
        record = redact_record(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": level.lower(),
                "source": source,
                "event": event,
                "metadata": metadata or {},
            }
        )
        with (self.log_dir / file_name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        if log_name == "errors" or level.lower() in {"error", "fatal"}:
            self.storage.record_error(
                severity="fatal" if level.lower() == "fatal" else "error",
                source=source,
                message=event,
                metadata=metadata or {},
            )
        return record

    def recent(self, log_name: str, limit: int = 50) -> list[dict[str, Any]]:
        file_name = LOG_NAMES.get(log_name, f"{log_name}.log")
        path = self.log_dir / file_name
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
