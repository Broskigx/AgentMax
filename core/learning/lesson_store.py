"""
LessonStore — SQLite-backed persistent store for auto-learning lessons.

Each lesson represents one failure (or success) the agent experienced,
with enough context to reconstruct what happened and how it was resolved.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

_DB_PATH = Path.home() / ".agentmax" / "autolearn.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id            TEXT PRIMARY KEY,
    ts            REAL    NOT NULL,
    context       TEXT    NOT NULL DEFAULT '',
    action_type   TEXT    NOT NULL DEFAULT '',
    input_summary TEXT    NOT NULL DEFAULT '',
    error         TEXT    NOT NULL DEFAULT '',
    goal_id       TEXT    NOT NULL DEFAULT '',
    task_id       TEXT    NOT NULL DEFAULT '',
    retry_count   INTEGER NOT NULL DEFAULT 0,
    resolved      INTEGER NOT NULL DEFAULT 0,
    resolution    TEXT    NOT NULL DEFAULT '',
    tags          TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS lessons_ts      ON lessons(ts DESC);
CREATE INDEX IF NOT EXISTS lessons_context ON lessons(context, resolved);
"""


@dataclass
class Lesson:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    ts: float = field(default_factory=time.time)
    context: str = ""
    action_type: str = ""
    input_summary: str = ""
    error: str = ""
    goal_id: str = ""
    task_id: str = ""
    retry_count: int = 0
    resolved: bool = False
    resolution: str = ""
    tags: list[str] = field(default_factory=list)


class LessonStore:
    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._path = db_path

    async def init(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def save(self, lesson: Lesson) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO lessons
                  (id, ts, context, action_type, input_summary, error,
                   goal_id, task_id, retry_count, resolved, resolution, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    lesson.id,
                    lesson.ts,
                    lesson.context,
                    lesson.action_type,
                    lesson.input_summary[:2000],
                    lesson.error[:2000],
                    lesson.goal_id,
                    lesson.task_id,
                    lesson.retry_count,
                    int(lesson.resolved),
                    lesson.resolution[:1000],
                    json.dumps(lesson.tags),
                ),
            )
            await db.commit()

    async def resolve(self, lesson_id: str, resolution: str) -> bool:
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                "UPDATE lessons SET resolved=1, resolution=? WHERE id=?",
                (resolution[:1000], lesson_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def list(
        self,
        *,
        limit: int = 200,
        unresolved_only: bool = False,
        context: str = "",
    ) -> list[dict[str, Any]]:
        filters = []
        params: list[Any] = []
        if unresolved_only:
            filters.append("resolved=0")
        if context:
            filters.append("context=?")
            params.append(context)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(limit)
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM lessons {where} ORDER BY ts DESC LIMIT ?", params
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def count(self) -> tuple[int, int]:
        """Returns (total, unresolved)."""
        async with aiosqlite.connect(self._path) as db, db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN resolved=0 THEN 1 ELSE 0 END) FROM lessons"
        ) as cur:
            row = await cur.fetchone()
        total = row[0] or 0
        unresolved = row[1] or 0
        return total, unresolved

    async def recent_context(self, n: int = 5) -> list[str]:
        """Return recent unresolved lessons as brief strings for prompt injection."""
        rows = await self.list(limit=n, unresolved_only=True)
        return [
            f"[{r['context']}/{r['action_type']}] {r['input_summary'][:80]}"
            f" → {r['error'][:120]}"
            for r in rows
        ]
