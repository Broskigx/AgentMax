"""Structured tool logging and event emission."""

from __future__ import annotations

from typing import Any

import structlog

from core.event_bus import Event
from core.tools.models import ToolDefinition, ToolRequest, ToolResult

log = structlog.get_logger(__name__)


class ToolLogger:
    def __init__(self, bus: Any = None, audit: Any = None, emit_structlog: bool = True) -> None:
        self.bus = bus
        self.audit = audit
        self.emit_structlog = emit_structlog

    async def started(self, definition: ToolDefinition, request: ToolRequest) -> None:
        payload = {
            "tool_id": definition.id,
            "category": definition.category,
            "task_id": request.task_id,
            "request_id": request.request_id,
            "dry_run": request.dry_run,
        }
        if self.emit_structlog:
            log.info("tool.started", **payload)
        await self._emit("tool.started", payload)

    async def paused(self, tool_id: str, task_id: str | None, reason: str) -> None:
        payload = {"tool_id": tool_id, "task_id": task_id, "reason": reason}
        if self.emit_structlog:
            log.warning("tool.paused", **payload)
        await self._emit("tool.paused", payload, priority=1)

    async def resumed(self, tool_id: str, task_id: str | None) -> None:
        payload = {"tool_id": tool_id, "task_id": task_id}
        if self.emit_structlog:
            log.info("tool.resumed", **payload)
        await self._emit("tool.resumed", payload)

    async def completed(self, result: ToolResult) -> None:
        payload = result.to_dict()
        if self.emit_structlog:
            log.info("tool.completed", **payload)
        await self._emit("tool.completed", payload)
        await self._audit("tool.completed", payload)

    async def failed(self, result: ToolResult) -> None:
        payload = result.to_dict()
        if self.emit_structlog:
            log.warning("tool.failed", **payload)
        await self._emit("tool.failed", payload, priority=1)
        await self._audit("tool.failed", payload)

    async def validation_failed(
        self,
        definition: ToolDefinition | None,
        request: ToolRequest,
        error: str,
    ) -> None:
        payload = {
            "tool_id": definition.id if definition else request.tool_id,
            "task_id": request.task_id,
            "request_id": request.request_id,
            "error": error,
        }
        if self.emit_structlog:
            log.warning("tool.validation_failed", **payload)
        await self._emit("tool.validation_failed", payload, priority=1)

    async def _emit(self, topic: str, payload: dict[str, Any], priority: int = 5) -> None:
        if self.bus:
            await self.bus.publish(
                Event(topic=topic, payload=payload, source="tools", priority=priority)
            )

    async def _audit(self, event: str, payload: dict[str, Any]) -> None:
        if self.audit and hasattr(self.audit, "log_event"):
            try:
                await self.audit.log_event(event, payload)
            except Exception:
                log.debug("tool.audit_failed", event=event)
