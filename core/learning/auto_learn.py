"""
AutoLearnEngine — self-improvement through failure memory.

Every time AgentMax fails at an action (shell command, package install,
tool call, goal loop abort…) this engine records the event in a local
SQLite database. The stored lessons serve two purposes:

  1. Real-time feedback: recent unresolved lessons are injected as
     context into the GoalEngine's AI decision prompt so the model can
     avoid repeating the same mistake in the same session.

  2. Training export: resolved lessons are exported as JSONL training
     pairs (see training_exporter.py) for future model fine-tuning.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from core.data_collection.redactor import redact_text
from core.learning.lesson_store import Lesson, LessonStore

log = structlog.get_logger()

_MAX_INPUT = 500
_MAX_ERROR = 800
_CONTEXT_LESSONS = 6  # lessons injected into AI prompt


class AutoLearnEngine:
    def __init__(self, store: LessonStore | None = None) -> None:
        self._store = store or LessonStore()
        self._ready = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Init                                                                 #
    # ------------------------------------------------------------------ #

    async def _init(self) -> None:
        if self._ready:
            return
        async with self._lock:
            if not self._ready:
                await self._store.init()
                self._ready = True

    # ------------------------------------------------------------------ #
    # Record                                                               #
    # ------------------------------------------------------------------ #

    async def record_failure(
        self,
        *,
        context: str,
        action_type: str = "",
        input_text: str = "",
        error: str = "",
        goal_id: str = "",
        task_id: str = "",
        tags: list[str] | None = None,
    ) -> str:
        """
        Record a failure event. Returns the lesson ID.

        Args:
            context:     Where the failure originated ('goal' | 'tool' | 'task' | 'chat').
            action_type: The specific action that failed ('run_shell', 'install_package', …).
            input_text:  The input/command that triggered the failure.
            error:       The error message or traceback.
            goal_id:     GoalEngine goal ID, if applicable.
            task_id:     Task ID, if applicable.
            tags:        Optional classification tags.
        """
        await self._init()
        lesson = Lesson(
            context=context,
            action_type=action_type,
            input_summary=redact_text(input_text[:_MAX_INPUT]),
            error=redact_text(error[:_MAX_ERROR]),
            goal_id=goal_id,
            task_id=task_id,
            tags=tags or [],
        )
        await self._store.save(lesson)
        log.info(
            "autolearn.failure",
            lesson_id=lesson.id,
            context=context,
            action_type=action_type,
        )
        return lesson.id

    async def record_success(
        self,
        *,
        lesson_id: str,
        resolution: str = "",
    ) -> bool:
        """Mark a lesson as resolved once the agent succeeds on retry."""
        await self._init()
        ok = await self._store.resolve(lesson_id, resolution or "Resolved on retry.")
        if ok:
            log.info("autolearn.resolved", lesson_id=lesson_id)
        return ok

    async def record_goal_iteration(
        self,
        *,
        goal_id: str,
        action_type: str,
        success: bool,
        input_text: str = "",
        error: str = "",
        resolution: str = "",
    ) -> str | None:
        """
        Convenience wrapper called from GoalEngine after each action.
        Records failure or resolves the most recent unresolved lesson
        for this goal+action_type pair.
        """
        if success:
            rows = await self._store.list(limit=20, unresolved_only=True, context="goal")
            for row in rows:
                if row["goal_id"] == goal_id and row["action_type"] == action_type:
                    await self._store.resolve(row["id"], resolution or "Succeeded on retry.")
                    return None
            return None
        return await self.record_failure(
            context="goal",
            action_type=action_type,
            input_text=input_text,
            error=error,
            goal_id=goal_id,
            tags=["goal_loop"],
        )

    # ------------------------------------------------------------------ #
    # Query                                                                #
    # ------------------------------------------------------------------ #

    async def recent_prompt_context(self) -> str:
        """
        Return recent unresolved lessons formatted for injection into
        the GoalEngine AI system prompt. Empty string if nothing to show.
        """
        await self._init()
        lines = await self._store.recent_context(_CONTEXT_LESSONS)
        if not lines:
            return ""
        joined = "\n".join(f"  • {line}" for line in lines)
        return f"\n\n[AutoLearn — recent failures to avoid]\n{joined}"

    async def stats(self) -> dict[str, Any]:
        await self._init()
        total, unresolved = await self._store.count()
        return {
            "total": total,
            "unresolved": unresolved,
            "resolved": total - unresolved,
        }

    async def get_lessons(
        self,
        limit: int = 100,
        unresolved_only: bool = False,
        context: str = "",
    ) -> list[dict[str, Any]]:
        await self._init()
        return await self._store.list(
            limit=limit, unresolved_only=unresolved_only, context=context
        )

    @property
    def store(self) -> LessonStore:
        return self._store
