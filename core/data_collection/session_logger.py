"""Session logger for AgentMax data collection.

All logs are stored LOCALLY under the resolved data dir (AGENTMAX_DATA_DIR or ~/.agentmax) /data/AgentMax_logs/ — never sent to any
server.  Used to improve AgentMax in future fine-tuning releases.

Directory layout::

    data/AgentMax_logs/
    ├── raw/          # Unfiltered logs (debug only)
    ├── redacted/     # PII-removed logs (safe for analysis)
    ├── approved/     # Marked as useful for training
    └── rejected/     # Marked as low-quality or failed

Each log entry includes the outcome field, which determines which directory
the entry is filed under at flush time.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import threading
from typing import Any

from core.data_collection.redactor import redact_record

log = logging.getLogger(__name__)

def _resolve_data_root() -> pathlib.Path:
    env = os.environ.get("AGENTMAX_DATA_DIR")
    if env:
        base = pathlib.Path(env)
    else:
        # Fallback for dev / non-Tauri runs — still user-local
        base = pathlib.Path.home() / ".agentmax"
    root = base / "data" / "AgentMax_logs"
    return root

_DATA_ROOT = _resolve_data_root()
_DIRS = ("raw", "redacted", "approved", "rejected")


def _ensure_dirs() -> None:
    for d in _DIRS:
        (_DATA_ROOT / d).mkdir(parents=True, exist_ok=True)


_ensure_dirs()

_lock = threading.Lock()
_default_logger: SessionLogger | None = None


class SessionLogger:
    """Per-session local logger.

    Usage::

        logger = SessionLogger()
        logger.log(
            user_message="open chrome",
            backend="lmstudio",
            outcome="solved",
            token_usage={"estimated": 150, "plan": "Free"},
        )

    Args:
        session_id: Optional explicit ID.  Auto-generated when omitted.
        data_root: Override the default ``data/AgentMax_logs/`` path.
    """

    def __init__(
        self,
        session_id: str | None = None,
        data_root: str | os.PathLike | None = None,
    ) -> None:
        if session_id:
            self.session_id = session_id
        else:
            import uuid

            self.session_id = f"session-{datetime.datetime.now(tz=datetime.UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"

        self._entries: list[dict[str, Any]] = []
        self._flushed: list[dict[str, Any]] = []
        self._root = pathlib.Path(data_root or _DATA_ROOT)
        _ensure_dirs()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, **fields: Any) -> None:
        """Append a single log entry.

        Supported fields mirror the TypeScript ``SessionLogEntry`` interface::

            user_message              str
            backend                   str
            model_status              str
            assistant_response_sanitized  str
            raw_response              str (only when debug=True)
            tool_calls                list[dict]
            tool_results              list[dict]
            approvals                 list[str]
            denials                   list[str]
            token_usage               dict (estimated / actual / plan)
            screenshot_metadata       list[dict]
            safety_flags              list[str]
            outcome                   str (solved / partial / failed / ...)
            error                     str
        """
        entry = {
            "ts": datetime.datetime.now(tz=datetime.UTC).isoformat(),
            "session_id": self.session_id,
            **fields,
        }
        with _lock:
            self._entries.append(entry)

    def flush(self, *, keep_in_memory: bool = False) -> None:
        """Write all pending entries to disk and optionally clear them.

        Args:
            keep_in_memory: If True, entries remain in memory for further use.
        """
        with _lock:
            if not self._entries:
                return
            pending = self._entries[:]
            if not keep_in_memory:
                self._entries.clear()

        now = datetime.datetime.now(tz=datetime.UTC)

        # --- Write raw (always, unless explicitly disabled) ---
        raw_path = self._root / "raw" / f"{self._file_stem(now)}.jsonl"
        self._write_jsonl(raw_path, pending)

        # --- Write redacted (always) ---
        redacted = [redact_record(e) for e in pending]
        redacted_path = self._root / "redacted" / f"{self._file_stem(now)}.jsonl"
        self._write_jsonl(redacted_path, redacted)

        # --- File by outcome ---
        for entry in redacted:
            outcome = str(entry.get("outcome") or "unknown").lower()
            if outcome in ("solved", "partial", "completed"):
                approved_path = self._root / "approved" / f"{self._file_stem(now)}.jsonl"
                self._write_jsonl(approved_path, [entry], append=True)
            elif outcome in ("failed", "cancelled", "model_unavailable", "error"):
                rejected_path = self._root / "rejected" / f"{self._file_stem(now)}.jsonl"
                self._write_jsonl(rejected_path, [entry], append=True)

        self._flushed.extend(pending)

    def summary(self) -> dict[str, Any]:
        """Return a summary of the current session."""
        with _lock:
            total = len(self._entries) + len(self._flushed)
        outcomes: dict[str, int] = {}
        for entry in self._flushed:
            o = str(entry.get("outcome") or "unknown")
            outcomes[o] = outcomes.get(o, 0) + 1
        return {
            "session_id": self.session_id,
            "total_entries": total,
            "flushed": len(self._flushed),
            "pending": len(self._entries),
            "outcomes": outcomes,
        }

    def get_pending(self) -> list[dict[str, Any]]:
        """Return a copy of pending (in-memory) entries."""
        with _lock:
            return list(self._entries)

    @staticmethod
    def _file_stem(dt: datetime.datetime) -> str:
        """Generate a file-stem from a datetime."""
        return dt.strftime("session_%Y%m%d_%H%M%S")

    @staticmethod
    def _write_jsonl(
        path: pathlib.Path,
        entries: list[dict[str, Any]],
        append: bool = False,
    ) -> None:
        """Write entries as JSONL (one JSON object per line)."""
        mode = "a" if append else "w"
        try:
            with open(path, mode, encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str))
                    f.write("\n")
        except OSError as exc:
            log.warning("session_logger.write_failed", path=str(path), error=str(exc))


# ------------------------------------------------------------------
# Module-level convenience
# ------------------------------------------------------------------


def get_session_logger(
    session_id: str | None = None,
    data_root: str | os.PathLike | None = None,
) -> SessionLogger:
    """Return (or create) the default module-level session logger."""
    global _default_logger
    if _default_logger is None:
        _default_logger = SessionLogger(session_id=session_id, data_root=data_root)
    return _default_logger


def log_entry(**fields: Any) -> None:
    """Convenience: log a single entry via the default logger."""
    get_session_logger().log(**fields)


def flush_session(*, keep_in_memory: bool = False) -> None:
    """Convenience: flush the default logger."""
    logger = get_session_logger()
    logger.flush(keep_in_memory=keep_in_memory)


__all__ = [
    "SessionLogger",
    "get_session_logger",
    "log_entry",
    "flush_session",
]
