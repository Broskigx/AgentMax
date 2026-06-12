"""Tool state and action history."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from core.tools.models import ToolResult, ToolStatus


@dataclass(slots=True)
class ToolRuntimeState:
    tool_id: str
    status: ToolStatus = ToolStatus.IDLE
    last_started_at: float | None = None
    last_completed_at: float | None = None
    last_error: str | None = None
    run_count: int = 0
    error_count: int = 0
    cooldown_until: float = 0.0


class ActionHistory:
    """Bounded history of executed tool results."""

    def __init__(self, max_items: int = 500) -> None:
        self._items: deque[ToolResult] = deque(maxlen=max_items)

    def append(self, result: ToolResult) -> None:
        self._items.append(result)

    def recent(self, limit: int = 50) -> list[ToolResult]:
        return list(self._items)[-limit:]

    def failures_for(self, tool_id: str, limit: int = 20) -> list[ToolResult]:
        return [item for item in self.recent(limit) if item.tool == tool_id and not item.success]


class ToolStateManager:
    """Tracks current status, cooldowns and recent execution history."""

    def __init__(self) -> None:
        self._states: dict[str, ToolRuntimeState] = {}
        self.history = ActionHistory()

    def state_for(self, tool_id: str) -> ToolRuntimeState:
        state = self._states.get(tool_id)
        if not state:
            state = ToolRuntimeState(tool_id=tool_id)
            self._states[tool_id] = state
        return state

    def can_run(self, tool_id: str) -> tuple[bool, str | None]:
        state = self.state_for(tool_id)
        now = time.monotonic()
        if now < state.cooldown_until:
            return False, f"cooldown_active:{int((state.cooldown_until - now) * 1000)}ms"
        return True, None

    def mark_started(self, tool_id: str) -> None:
        state = self.state_for(tool_id)
        state.status = ToolStatus.RUNNING
        state.last_started_at = time.time()

    def mark_paused(self, tool_id: str, reason: str) -> None:
        state = self.state_for(tool_id)
        state.status = ToolStatus.PAUSED_BY_USER
        state.last_error = reason

    def mark_result(self, result: ToolResult, cooldown_ms: int = 0) -> None:
        state = self.state_for(result.tool)
        state.status = ToolStatus.COMPLETED if result.success else ToolStatus.FAILED
        state.last_completed_at = time.time()
        state.last_error = result.error
        state.run_count += 1
        if not result.success:
            state.error_count += 1
        if cooldown_ms > 0:
            state.cooldown_until = time.monotonic() + (cooldown_ms / 1000.0)
        self.history.append(result)

    def snapshot(self) -> dict[str, Any]:
        return {
            "states": {
                tool_id: {
                    "status": state.status.value,
                    "last_started_at": state.last_started_at,
                    "last_completed_at": state.last_completed_at,
                    "last_error": state.last_error,
                    "run_count": state.run_count,
                    "error_count": state.error_count,
                    "cooldown_until": state.cooldown_until,
                }
                for tool_id, state in sorted(self._states.items())
            },
            "history": [item.to_dict() for item in self.history.recent(25)],
        }
