from __future__ import annotations

import asyncio
import sys
import time
from collections import deque
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_FLUSH_INTERVAL_SEC = 60
_MAX_QUEUE = 500
_BATCH_SIZE = 100


class TelemetryClient:
    def __init__(
        self,
        *,
        enabled: bool,
        client_version: str,
        platform_str: str | None = None,
    ) -> None:
        self._enabled = enabled
        self._client_version = client_version
        self._platform = platform_str or sys.platform
        self._queue: deque[dict] = deque(maxlen=_MAX_QUEUE)
        self._flush_task: asyncio.Task | None = None
        self._http_client: Any = None  # set by start()

    def track(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled:
            return
        self._queue.append(
            {
                "event_name": event_name,
                "client_version": self._client_version,
                "platform": self._platform,
                "payload": payload or {},
                "_ts": time.time(),
            }
        )

    def track_error(self, exc: Exception, context: str = "") -> None:
        """Track an error event -- only exception type, no traceback."""
        self.track(
            "error.crash",
            {
                "exception_type": type(exc).__name__,
                "context": context[:128],
            },
        )

    def track_task(self, task_type: str, duration_ms: float, success: bool) -> None:
        self.track(
            "task.completed" if success else "task.failed",
            {
                "task_type": task_type,
                "duration_ms": round(duration_ms),
            },
        )

    def track_feature(self, feature_name: str, **metadata: Any) -> None:
        self.track(f"feature.{feature_name}", dict(metadata))

    async def start(self, http_client: Any) -> None:
        self._http_client = http_client
        self._flush_task = asyncio.create_task(self._flush_loop(), name="telemetry-flush")
        self.track("startup", {"platform": self._platform})

    async def stop(self) -> None:
        self.track("shutdown", {"queued_events": len(self._queue)})
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # Final flush
        if self._queue and self._http_client:
            await self._flush_batch()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL_SEC)
            if self._queue and self._http_client:
                await self._flush_batch()

    async def _flush_batch(self) -> None:
        if not self._queue:
            return
        batch = []
        for _ in range(min(_BATCH_SIZE, len(self._queue))):
            if self._queue:
                ev = self._queue.popleft()
                ev.pop("_ts", None)  # internal field, not sent
                batch.append(ev)

        try:
            await self._http_client.post(
                "/v1/telemetry/batch",
                json={"events": batch},
            )
            log.debug("telemetry.flushed", count=len(batch))
        except Exception as exc:
            log.debug("telemetry.flush_failed", error=str(exc))
            # Events are dropped on failure -- telemetry is best-effort
