"""Audit log -- append-only JSONL with HMAC-SHA256 integrity signing per entry."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any


class AuditLog:
    """
    Append-only structured audit trail.

    Each entry is signed with HMAC-SHA256 so tampering can be detected
    offline by re-verifying the 'sig' field against the entry content.
    Key is sourced from AGENTMAX_AUDIT_HMAC_KEY env var (empty = no signing).
    """

    def __init__(self, path: Path | str, hmac_key: str = "") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._key: bytes = (
            hmac_key.encode() or os.environ.get("AGENTMAX_AUDIT_HMAC_KEY", "").encode()
        )
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._writer_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._writer_task = asyncio.create_task(self._write_loop(), name="audit-log")

    async def stop(self) -> None:
        await self._queue.join()
        if self._writer_task:
            self._writer_task.cancel()

    async def log_event(self, event_type: str, data: dict[str, Any]) -> None:
        entry = {"ts": time.time(), "type": event_type, **data}
        if self._key:
            body = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            entry["sig"] = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
        await self._queue.put(entry)

    async def _write_loop(self) -> None:
        while True:
            try:
                entry = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await asyncio.to_thread(self._append, entry)
                self._queue.task_done()
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def _append(self, entry: dict) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def verify_log(self) -> list[dict]:
        """
        Re-read the log and return a list of entries where HMAC verification failed.
        Returns [] if no key is set (can't verify) or all entries pass.
        """
        if not self._key:
            return []

        failures: list[dict] = []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

        for line in lines:
            try:
                entry = json.loads(line)
                sig = entry.pop("sig", None)
                if sig is None:
                    continue
                body = json.dumps(entry, sort_keys=True, separators=(",", ":"))
                expected = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(sig, expected):
                    failures.append(entry)
            except Exception:
                pass

        return failures
