"""Base agent -- shared contract for all AgentMax agents."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
import structlog.contextvars as _ctx

from core.event_bus import Event, EventBus
from core.utils.profiler import PipelineProfiler

if TYPE_CHECKING:
    from core.config import AgentMaxConfig
    from core.memory.long_term import LongTermMemory
    from core.memory.short_term import ShortTermMemory
    from core.memory.visual_memory import VisualMemory
    from core.runtime import AgentMaxRuntime
    from core.security.audit_log import AuditLog
    from core.security.permission_manager import PermissionManager


log = structlog.get_logger(__name__)


class AgentState(Enum):
    IDLE = auto()
    BUSY = auto()
    PAUSED = auto()
    ERROR = auto()
    STOPPED = auto()


@dataclass
class AgentContext:
    """Shared dependency container -- injected into every agent."""

    config: AgentMaxConfig
    bus: EventBus
    capture: Any
    ocr: Any
    accessibility: Any
    stm: ShortTermMemory
    ltm: LongTermMemory
    visual_memory: VisualMemory
    security: PermissionManager
    audit: AuditLog
    runtime: AgentMaxRuntime


@dataclass
class ActionResult:
    success: bool
    data: Any = None
    error: str | None = None
    error_code: str | None = None
    confidence: float = 1.0
    reasoning: str = ""
    duration_ms: float = 0.0
    metadata: dict[str, Any] | None = None


@dataclass
class AgentCapability:
    name: str
    description: str
    requires_screen: bool = False
    requires_input: bool = False
    requires_network: bool = False


class BaseAgent(ABC):
    """
    Abstract base for all AgentMax agents.

    Lifecycle:  __init__ → start() → [run loop] → stop()

    Each agent:
      - Has a unique name
      - Subscribes to relevant bus topics
      - Exposes capabilities
      - Reports state
      - Supports emergency stop
    """

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx
        self.bus = ctx.bus
        self.config = ctx.config
        self._state = AgentState.IDLE
        self._stop_event = asyncio.Event()
        self._panic = False
        self._task: asyncio.Task[None] | None = None
        self._log = structlog.get_logger(self.__class__.__name__)
        self._metrics: dict[str, int | float] = {
            "actions": 0,
            "errors": 0,
            "avg_latency_ms": 0.0,
        }

    # ──────────────────────────────────────────────────────────────
    # Identity
    # ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def capabilities(self) -> list[AgentCapability]:
        return []

    @property
    def state(self) -> AgentState:
        return self._state

    # ──────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._subscribe()
        self._task = asyncio.create_task(self._run_loop(), name=f"agent-{self.name}")
        self._log.info("agent.started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
        self._state = AgentState.STOPPED
        self._log.info("agent.stopped")

    def emergency_stop(self) -> None:
        self._panic = True
        self._stop_event.set()
        self._state = AgentState.STOPPED

    # ──────────────────────────────────────────────────────────────
    # Subscriptions (override in subclass)
    # ──────────────────────────────────────────────────────────────

    def _subscribe(self) -> None:
        self.bus.subscribe("system.panic", self._on_panic)

    async def _on_panic(self, _event: Event) -> None:
        self.emergency_stop()

    # ──────────────────────────────────────────────────────────────
    # Run loop (override in subclass)
    # ──────────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set() and not self._panic:
            try:
                await self._tick()
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._metrics["errors"] = int(self._metrics["errors"]) + 1
                self._log.error("agent.tick_error", error=str(exc))
                self._state = AgentState.ERROR
                await asyncio.sleep(0.5)

    async def _tick(self) -> None:  # noqa: B027 - optional per-loop hook, default no-op
        """Optional periodic hook run on each agent loop iteration."""

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    async def emit(
        self,
        topic: str,
        payload: Any,
        priority: int = 5,
        correlation_id: str | None = None,
    ) -> None:
        # Carry current correlation ID forward automatically if not overridden.
        cid = correlation_id or _ctx.get_contextvars().get("task_id", "")
        await self.bus.publish(
            Event(topic=topic, payload=payload, source=self.name, priority=priority)
        )
        if cid:
            # Embed the correlation ID in the payload if it's a dict so
            # downstream agents can read it without inspecting log lines.
            if isinstance(payload, dict):
                payload.setdefault("_task_id", cid)

    async def log_terminal(self, text: str, type: str = "stdout") -> None:
        """Stream a message directly to the UI terminal sandbox."""
        await self.emit("terminal.output", {"text": text, "type": type})

    @asynccontextmanager
    async def task_context(self, task_id: str) -> AsyncGenerator[PipelineProfiler, None]:
        """
        Bind a task_id to all structlog calls within this async scope.
        Also creates and yields a PipelineProfiler for span timing.

        Usage in Supervisor:
            async with self.task_context(record.request.id) as profiler:
                with profiler.span("planning"):
                    await self._phase_plan(record)
        """
        _ctx.bind_contextvars(task_id=task_id, agent=self.name)
        profiler = PipelineProfiler(task_id=task_id)
        try:
            yield profiler
        finally:
            profiler.log_report()
            _ctx.unbind_contextvars("task_id", "agent")

    def _track_action(self, duration_ms: float, success: bool) -> None:
        n = self._metrics["actions"] = int(self._metrics["actions"]) + 1
        prev_avg = float(self._metrics["avg_latency_ms"])
        self._metrics["avg_latency_ms"] = prev_avg + (duration_ms - prev_avg) / n
        if not success:
            self._metrics["errors"] = int(self._metrics["errors"]) + 1

    @property
    def metrics(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self._state.name,
            **self._metrics,
        }

    def new_id(self) -> str:
        return str(uuid4())
