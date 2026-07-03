"""SQLite storage service for the AgentMax closed beta."""

from __future__ import annotations

import csv
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.data_collection.redactor import redact_record, redact_text

from .config import BetaConfig, get_beta_config

GLOBAL_SETTINGS_USER_ID = "__global_settings__"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(data: Any) -> str:
    return json.dumps(redact_record(data or {}), ensure_ascii=False, separators=(",", ":"))


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(
        part in lowered for part in ("api_key", "token", "secret", "password", "cookie", "jwt")
    )


class StorageService:
    """Central SQLite gateway. No loose beta queries should live elsewhere."""

    def __init__(self, db_path: str | Path | None = None, config: BetaConfig | None = None) -> None:
        self.config = config or get_beta_config()
        self.db_path = Path(db_path) if db_path else self.config.sqlite_abs_path
        if not self.db_path.is_absolute():
            self.db_path = Path.cwd() / self.db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                ("0001_closed_beta", _now()),
            )

    def reset_dev(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        for suffix in ("-wal", "-shm"):
            p = Path(f"{self.db_path}{suffix}")
            if p.exists():
                p.unlink()
        self.migrate()

    def status(self) -> dict[str, Any]:
        self.migrate()
        with self.connect() as conn:
            tables = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            feedback_count = conn.execute("SELECT COUNT(*) AS n FROM tester_feedback").fetchone()[
                "n"
            ]
        return {
            "ok": True,
            "path": str(self.db_path),
            "tables": tables,
            "users": user_count,
            "feedback": feedback_count,
        }

    def ensure_user(
        self,
        *,
        user_id: str | None = None,
        email: str | None = None,
        display_name: str | None = None,
        beta_key: str | None = None,
    ) -> str:
        self.migrate()
        uid = user_id or "local_tester"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users(id,email,display_name,beta_key,plan,created_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    email=excluded.email,
                    display_name=excluded.display_name,
                    beta_key=excluded.beta_key
                """,
                (
                    uid,
                    redact_text(email) if email else None,
                    redact_text(display_name) if display_name else None,
                    redact_text(beta_key) if beta_key else None,
                    "closed_beta",
                    _now(),
                ),
            )
        return uid

    def create_conversation(self, user_id: str, title: str = "Closed beta session") -> str:
        self.migrate()
        cid = _new_id("conv")
        ts = _now()
        self.ensure_user(user_id=user_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations(id,user_id,title,created_at,updated_at,archived)
                VALUES(?,?,?,?,?,0)
                """,
                (cid, user_id, redact_text(title), ts, ts),
            )
        return cid

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.migrate()
        mid = _new_id("msg")
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError("invalid message role")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO messages(id,conversation_id,role,content,metadata_json,created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (mid, conversation_id, role, redact_text(content), _json(metadata), _now()),
            )
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (_now(), conversation_id),
            )
        return mid

    def create_task(
        self,
        *,
        user_id: str,
        goal: str,
        conversation_id: str | None = None,
        status: str = "queued",
    ) -> str:
        self.migrate()
        if status not in TASK_STATUSES:
            raise ValueError("invalid task status")
        tid = _new_id("task")
        ts = _now()
        self.ensure_user(user_id=user_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_tasks(id,user_id,conversation_id,goal,status,result_json,error,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (tid, user_id, conversation_id, redact_text(goal), status, None, None, ts, ts),
            )
        return tid

    def update_task_status(
        self,
        task_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.migrate()
        if status not in TASK_STATUSES:
            raise ValueError("invalid task status")
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agent_tasks
                SET status=?, result_json=?, error=?, updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    _json(result) if result is not None else None,
                    redact_text(error) if error else None,
                    _now(),
                    task_id,
                ),
            )
        self.add_agent_event(task_id=task_id, event_type=f"task.{status}", payload=result or {})

    def add_agent_event(
        self,
        *,
        task_id: str | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        self.migrate()
        eid = _new_id("evt")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_events(id,task_id,event_type,payload_json,created_at)
                VALUES(?,?,?,?,?)
                """,
                (eid, task_id, redact_text(event_type), _json(payload), _now()),
            )
        return eid

    def save_feedback(
        self,
        *,
        user_id: str | None = None,
        conversation_id: str | None = None,
        task_id: str | None = None,
        rating: int | None = None,
        message: str = "",
        category: str = "other",
        screenshot_path: str | None = None,
        logs_path: str | None = None,
    ) -> str:
        self.migrate()
        if category not in FEEDBACK_CATEGORIES:
            category = "other"
        if rating is not None:
            rating = max(1, min(5, int(rating)))
        fid = _new_id("fb")
        if user_id:
            self.ensure_user(user_id=user_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tester_feedback(
                  id,user_id,conversation_id,task_id,rating,message,category,
                  screenshot_path,logs_path,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    fid,
                    user_id,
                    conversation_id,
                    task_id,
                    rating,
                    redact_text(message),
                    category,
                    redact_text(screenshot_path) if screenshot_path else None,
                    redact_text(logs_path) if logs_path else None,
                    _now(),
                ),
            )
        return fid

    def record_error(
        self,
        *,
        severity: str,
        source: str,
        message: str,
        stack: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.migrate()
        if severity not in {"info", "warn", "error", "fatal"}:
            severity = "error"
        eid = _new_id("err")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_errors(id,severity,source,message,stack,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    eid,
                    severity,
                    redact_text(source),
                    redact_text(message),
                    redact_text(stack) if stack else None,
                    _json(metadata),
                    _now(),
                ),
            )
        return eid

    def set_setting(self, key: str, value: Any, user_id: str | None = None) -> str:
        self.migrate()
        if _sensitive_key(key):
            raise ValueError("refusing to persist secret-like setting key")
        settings_user_id = user_id or GLOBAL_SETTINGS_USER_ID
        self.ensure_user(user_id=settings_user_id)
        sid = _new_id("setting")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(id,user_id,key,value_json,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(user_id,key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (sid, settings_user_id, key, _json(value), _now()),
            )
        return sid

    def get_setting(self, key: str, user_id: str | None = None, default: Any = None) -> Any:
        self.migrate()
        settings_user_id = user_id or GLOBAL_SETTINGS_USER_ID
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM settings WHERE key=? AND user_id=? ORDER BY updated_at DESC LIMIT 1",
                (key, settings_user_id),
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    def recent_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        self.migrate()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id,user_id,conversation_id,goal,status,result_json,error,created_at,updated_at
                FROM agent_tasks ORDER BY updated_at DESC LIMIT ?
                """,
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        self.migrate()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id,severity,source,message,stack,metadata_json,created_at
                FROM app_errors ORDER BY created_at DESC LIMIT ?
                """,
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def feedback_rows(self) -> list[dict[str, Any]]:
        self.migrate()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id,user_id,conversation_id,task_id,rating,message,category,
                       screenshot_path,logs_path,created_at
                FROM tester_feedback ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def export_feedback(self, output_dir: str | Path | None = None) -> dict[str, str]:
        self.migrate()
        out = Path(output_dir or (Path.cwd() / "beta_exports"))
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        rows = self.feedback_rows()
        json_path = out / f"agentmax-feedback-{stamp}.json"
        csv_path = out / f"agentmax-feedback-{stamp}.csv"
        json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            fields = [
                "id",
                "user_id",
                "conversation_id",
                "task_id",
                "rating",
                "message",
                "category",
                "screenshot_path",
                "logs_path",
                "created_at",
            ]
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return {"json": str(json_path), "csv": str(csv_path)}


TASK_STATUSES = {"queued", "running", "paused", "completed", "failed", "cancelled", "stopped"}
FEEDBACK_CATEGORIES = {"bug", "idea", "ux", "performance", "security", "other"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT,
  display_name TEXT,
  beta_key TEXT,
  plan TEXT NOT NULL DEFAULT 'closed_beta',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
  content TEXT NOT NULL,
  metadata_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tasks (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','paused','completed','failed','cancelled','stopped')),
  result_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_events (
  id TEXT PRIMARY KEY,
  task_id TEXT REFERENCES agent_tasks(id) ON DELETE SET NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tester_feedback (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  task_id TEXT REFERENCES agent_tasks(id) ON DELETE SET NULL,
  rating INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 5),
  message TEXT NOT NULL,
  category TEXT NOT NULL CHECK(category IN ('bug','idea','ux','performance','security','other')),
  screenshot_path TEXT,
  logs_path TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_errors (
  id TEXT PRIMARY KEY,
  severity TEXT NOT NULL CHECK(severity IN ('info','warn','error','fatal')),
  source TEXT NOT NULL,
  message TEXT NOT NULL,
  stack TEXT,
  metadata_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id,key)
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  task_id TEXT REFERENCES agent_tasks(id) ON DELETE SET NULL,
  status TEXT NOT NULL,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_updated ON agent_tasks(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_events_task_created ON agent_events(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON tester_feedback(created_at);
CREATE INDEX IF NOT EXISTS idx_errors_created ON app_errors(created_at);
"""
