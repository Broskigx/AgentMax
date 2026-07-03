"""Lock-free async event bus -- backbone for inter-agent communication."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import structlog

log = structlog.get_logger(__name__)


# ── Priority constants ────────────────────────────────────────────────────────
#
# Use these instead of raw integers so code is self-documenting and priorities
# can be tuned in one place.
#
# CRITICAL (0) -- system.panic, security violations, emergency stop.
#   Dispatched from a dedicated fast-path queue that is drained BEFORE any
#   normal event is processed.  A CRITICAL event that arrives while a normal
#   event's handlers are running will be dispatched on the very next loop tick
#   (typically < 1 ms latency).
#
# HIGH (1)     -- user-initiated commands, license heartbeat alerts.
# NORMAL (5)   -- routine agent-to-agent messages.
# LOW (10)     -- telemetry, logging, background housekeeping.

PRIORITY_CRITICAL = 0
PRIORITY_HIGH = 1
PRIORITY_NORMAL = 5
PRIORITY_LOW = 10


@dataclass(slots=True)
class Event:
    topic: str
    payload: Any
    source: str = "system"
    id: str = field(default_factory=lambda: str(uuid4()))
    priority: int = PRIORITY_NORMAL

    def __lt__(self, other: Event) -> bool:
        return self.priority < other.priority


Handler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """
    Priority-aware async pub/sub bus.

    Topics follow a dot-path hierarchy:
      agent.supervisor.task_assigned
      vision.screenshot.captured
      input.mouse.moved
      system.panic
    Wildcards: subscribe to "vision.*" receives all vision subtopics.

    Priority fast-path:
      Events with priority == PRIORITY_CRITICAL (0) are placed in a separate
      queue (_critical_queue) and are ALWAYS dispatched before any pending
      normal-priority events.  This guarantees that panic / security events
      interrupt queued work without waiting behind hundreds of telemetry
      messages.
    """

    def __init__(self, queue_size: int = 4096) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        # Normal-priority queue -- all non-critical events land here.
        self._queue: asyncio.PriorityQueue[tuple[int, Event]] = asyncio.PriorityQueue(
            maxsize=queue_size
        )
        # Critical fast-path queue -- unbounded (panic events must never block).
        self._critical_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._dispatch_task: asyncio.Task[None] | None = None
        # Loop the bus runs on -- captured in start() so publish_sync can hop
        # threads with call_soon_threadsafe (get_event_loop() raises inside a
        # worker thread, which is exactly where publish_sync is called from).
        self._loop: asyncio.AbstractEventLoop | None = None
        # Strong refs to in-flight handler tasks; asyncio only keeps weak ones,
        # so without this a handler task can be garbage-collected mid-run.
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._metrics: dict[str, int] = defaultdict(int)

    # ──────────────────────────────────────────────────────────────
    # Subscription
    # ──────────────────────────────────────────────────────────────

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)
        log.debug("bus.subscribed", topic=topic, handler=handler.__qualname__)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        try:
            self._subscribers[topic].remove(handler)
        except ValueError:
            pass

    # ──────────────────────────────────────────────────────────────
    # Publishing
    # ──────────────────────────────────────────────────────────────

    async def publish(self, event: Event) -> None:
        if event.priority == PRIORITY_CRITICAL:
            await self._critical_queue.put(event)
        else:
            await self._queue.put((event.priority, event))
        self._metrics["published"] += 1

    def publish_sync(self, event: Event) -> None:
        """Non-async publish -- safe to call from sync context (e.g. watchdog thread)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            self._metrics["dropped_sync"] += 1
            log.warning("event_bus.publish_sync_dropped", topic=event.topic, reason="not_started")
            return
        loop.call_soon_threadsafe(self._enqueue_nowait, event)

    def _enqueue_nowait(self, event: Event) -> None:
        """Enqueue from the bus loop thread without awaiting (publish_sync path)."""
        try:
            if event.priority == PRIORITY_CRITICAL:
                self._critical_queue.put_nowait(event)
            else:
                self._queue.put_nowait((event.priority, event))
            self._metrics["published"] += 1
        except asyncio.QueueFull:
            self._metrics["dropped_sync"] += 1
            log.warning("event_bus.publish_sync_dropped", topic=event.topic, reason="queue_full")

    async def panic(self, payload: Any, source: str = "system") -> None:
        """
        Shortcut for publishing a system.panic CRITICAL event.
        Guaranteed to be dispatched before any queued normal events.
        """
        await self.publish(
            Event(
                topic="system.panic",
                payload=payload,
                source=source,
                priority=PRIORITY_CRITICAL,
            )
        )

    # ──────────────────────────────────────────────────────────────
    # Dispatch loop
    # ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._dispatch_task = asyncio.create_task(self._dispatch_loop(), name="event-bus")
        log.info("event_bus.started")

    async def stop(self) -> None:
        self._running = False
        if self._dispatch_task:
            self._dispatch_task.cancel()
        log.info("event_bus.stopped", metrics=dict(self._metrics))

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                # ── Fast-path: drain CRITICAL queue first ──────────
                # Check without waiting so normal events don't block panic handling.
                if not self._critical_queue.empty():
                    event = self._critical_queue.get_nowait()
                    await self._dispatch(event, urgent=True)
                    self._critical_queue.task_done()
                    continue

                # ── Normal queue -- short timeout so we re-check critical often ──
                try:
                    _, event = await asyncio.wait_for(self._queue.get(), timeout=0.01)
                except TimeoutError:
                    continue

                # Before dispatching the normal event, do one final critical check.
                # If a critical event arrived between the two awaits above, handle
                # it first and put the normal event back.
                if not self._critical_queue.empty():
                    await self._queue.put((event.priority, event))
                    continue

                await self._dispatch(event)
                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("event_bus.dispatch_error", error=str(exc))

    async def _dispatch(self, event: Event, *, urgent: bool = False) -> None:
        handlers = self._collect_handlers(event.topic)
        if not handlers:
            return
        self._metrics["dispatched"] += 1
        if urgent:
            self._metrics["critical_dispatched"] += 1

        tasks = [asyncio.create_task(h(event)) for h in handlers]

        if urgent:
            # For critical events, use a short timeout so a slow handler cannot
            # block the next critical event (e.g. two rapid panic signals).
            done, pending = await asyncio.wait(tasks, timeout=2.0)
            for t in pending:
                t.cancel()

            for t in done:
                try:
                    await t
                except Exception as exc:
                    log.error("event_bus.critical_handler_error", topic=event.topic, error=str(exc))
        else:
            # Normal events run in background to keep bus responsive.
            # The callback logs unhandled exceptions so they don't vanish silently.
            def _log_exc(t: asyncio.Task, _topic: str = event.topic) -> None:
                self._handler_tasks.discard(t)
                if not t.cancelled() and (exc := t.exception()):
                    log.warning("event_bus.handler_error", topic=_topic, error=str(exc))

            for task in tasks:
                self._handler_tasks.add(task)
                task.add_done_callback(_log_exc)

    def _collect_handlers(self, topic: str) -> list[Handler]:
        handlers: list[Handler] = []
        handlers.extend(self._subscribers.get(topic, []))
        parts = topic.split(".")
        for i in range(1, len(parts)):
            wildcard = ".".join(parts[:i]) + ".*"
            handlers.extend(self._subscribers.get(wildcard, []))
        handlers.extend(self._subscribers.get("*", []))
        return handlers

    # ──────────────────────────────────────────────────────────────
    # Diagnostics
    # ──────────────────────────────────────────────────────────────

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def critical_queue_depth(self) -> int:
        return self._critical_queue.qsize()

    @property
    def metrics(self) -> dict[str, int]:
        return dict(self._metrics)


# Module-level singleton
_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
