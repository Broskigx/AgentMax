"""Session-scoped conversation history for multi-turn AI interactions."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

MessageRole = Literal["user", "assistant"]

_MAX_CONTENT_CHARS = 6000
_MAX_TURNS = 20


@dataclass
class ConversationMessage:
    role: MessageRole
    content: str
    timestamp: float = field(default_factory=time.time)
    task_id: str | None = None


class ConversationManager:
    """
    Rolling conversation window for a session.

    Provides Anthropic-format message history for multi-turn context.
    Thread-safe for single-event-loop async use.
    """

    def __init__(self) -> None:
        self._messages: list[ConversationMessage] = []
        self._last_task_desc: str | None = None

    # ── Public API ────────────────────────────────────────────────

    def add_user(self, content: str, *, task_id: str | None = None) -> None:
        self._messages.append(ConversationMessage(role="user", content=content, task_id=task_id))
        self._trim()

    def add_assistant(self, content: str, *, task_id: str | None = None) -> None:
        self._messages.append(
            ConversationMessage(role="assistant", content=content, task_id=task_id)
        )
        if task_id:
            self._last_task_desc = content
        self._trim()

    def update_last_task_desc(self, description: str) -> None:
        """Record the most recently submitted task for follow-up context."""
        self._last_task_desc = description

    @property
    def last_task_description(self) -> str | None:
        return self._last_task_desc

    def get_history(self, *, max_turns: int = _MAX_TURNS) -> list[dict]:
        """Return Anthropic-compatible message list (text only, no images)."""
        window = self._messages[-(max_turns * 2) :]
        result = []
        for msg in window:
            content = msg.content
            if len(content) > _MAX_CONTENT_CHARS:
                content = content[:_MAX_CONTENT_CHARS] + "\n[…truncated]"
            result.append({"role": msg.role, "content": content})
        return result

    def get_messages(self) -> list[ConversationMessage]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()
        self._last_task_desc = None

    # ── Internal ──────────────────────────────────────────────────

    def _trim(self) -> None:
        cap = _MAX_TURNS * 2
        if len(self._messages) > cap:
            self._messages = self._messages[-cap:]
