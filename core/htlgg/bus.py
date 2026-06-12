from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

import structlog

from core.data_collection.redactor import redact_record
from core.event_bus import Event

from .codec import encode_record
from .redis_bridge import HtlggRedisBridge
from .schema import (
    ControlRecord,
    DecisionRecord,
    ElementCandidate,
    ExecutionRecord,
    HtlggEnvelope,
    StateRecord,
)
from .validator import validate_record

log = structlog.get_logger(__name__)


class HtlggBus:
    def __init__(
        self,
        *,
        event_bus: Any = None,
        bridge: HtlggRedisBridge | None = None,
        max_items: int = 256,
    ) -> None:
        self.event_bus = event_bus
        self.bridge = bridge
        self._items: deque[dict[str, Any]] = deque(maxlen=max(1, max_items))

    async def emit(self, envelope: HtlggEnvelope) -> bool:
        try:
            validate_record(envelope.record)
            encoded = encode_record(envelope.record)
            topic = self._topic(envelope.record)
            public = redact_record(
                {
                    "version": envelope.version,
                    "record": encoded,
                    "session_id": envelope.session_id,
                    "task_id": envelope.task_id,
                    "timestamp": envelope.timestamp,
                    "metadata": envelope.metadata,
                }
            )
            self._items.append(public)
            if self.event_bus is not None:
                self._background(
                    self.event_bus.publish(
                        Event(
                            topic=f"htlgg.{topic}",
                            payload=public,
                            source="htlgg",
                        )
                    ),
                    "event_publish",
                )
            if self.bridge is not None:
                self._background(
                    asyncio.to_thread(self.bridge.publish, topic, envelope, encoded),
                    "bridge_publish",
                )
            return True
        except Exception as exc:
            log.warning("htlgg.emit_failed", error=str(exc))
            return False

    def recent(self) -> list[dict[str, Any]]:
        return list(self._items)

    @staticmethod
    def _background(coro: Any, operation: str) -> None:
        task = asyncio.create_task(coro)

        def _done(completed: asyncio.Task[Any]) -> None:
            if completed.cancelled():
                return
            if exc := completed.exception():
                log.warning("htlgg.background_failed", operation=operation, error=str(exc))

        task.add_done_callback(_done)

    @staticmethod
    def _topic(record: object) -> str:
        if isinstance(record, (StateRecord, ElementCandidate)):
            return "state"
        if isinstance(record, DecisionRecord):
            return "decision"
        if isinstance(record, ExecutionRecord):
            return "exec_result"
        if isinstance(record, ControlRecord):
            return "error"
        return "error"


__all__ = ["HtlggBus"]
