"""Normalize all tool outputs into one stable result contract."""

from __future__ import annotations

from typing import Any

from core.agents.base_agent import ActionResult
from core.tools.models import ToolRequest, ToolResult, ToolStatus


class ToolResultNormalizer:
    def from_action_result(
        self,
        request: ToolRequest,
        action: ActionResult,
        *,
        tool_id: str | None = None,
        attempts: int = 1,
        fallback_used: str | None = None,
    ) -> ToolResult:
        output = action.data if isinstance(action.data, dict) else {"value": action.data}
        return ToolResult(
            success=action.success,
            tool=tool_id or request.tool_id,
            input=request.input,
            output=output,
            error=action.error,
            duration_ms=action.duration_ms,
            confidence=action.confidence,
            next_recommended_action=None if action.success else "retry_or_fallback",
            request_id=request.request_id,
            task_id=request.task_id,
            status=ToolStatus.COMPLETED if action.success else ToolStatus.FAILED,
            error_code=None if action.success else "tool.action_failed",
            attempts=attempts,
            fallback_used=fallback_used,
            completed_at=None,
        )

    def failure(
        self,
        request: ToolRequest,
        code: str,
        message: str,
        *,
        duration_ms: float = 0.0,
        attempts: int = 1,
    ) -> ToolResult:
        return ToolResult(
            success=False,
            tool=request.tool_id,
            input=request.input,
            output={},
            error=message,
            duration_ms=duration_ms,
            confidence=0.0,
            next_recommended_action="review_tool_input",
            request_id=request.request_id,
            task_id=request.task_id,
            status=ToolStatus.FAILED,
            error_code=code,
            attempts=attempts,
        )

    def success(
        self,
        request: ToolRequest,
        output: dict[str, Any] | None = None,
        *,
        duration_ms: float = 0.0,
        confidence: float = 1.0,
        tool_id: str | None = None,
        attempts: int = 1,
        fallback_used: str | None = None,
    ) -> ToolResult:
        return ToolResult(
            success=True,
            tool=tool_id or request.tool_id,
            input=request.input,
            output=output or {},
            error=None,
            duration_ms=duration_ms,
            confidence=confidence,
            request_id=request.request_id,
            task_id=request.task_id,
            status=ToolStatus.COMPLETED,
            attempts=attempts,
            fallback_used=fallback_used,
        )
