"""Supervisor Agent -- orchestrates the full multi-agent pipeline for each task."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any
from uuid import uuid4

import structlog

from core.agents.base_agent import (
    ActionResult,
    AgentCapability,
    AgentContext,
    BaseAgent,
)
from core.ai.thinking_engine import get_thinking_engine
from core.event_bus import Event
from core.htlgg import DecisionRecord, HtlggEnvelope, RiskLevel
from core.tools.executor import ToolExecutor
from core.tools.logger import ToolLogger
from core.tools.registry import ToolRegistry
from core.tools.state import ToolStateManager
from core.utils.validation import TaskValidationError, validate_task_description

log = structlog.get_logger(__name__)


class TaskStatus(Enum):
    QUEUED = auto()
    PLANNING = auto()
    EXECUTING = auto()
    VALIDATING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class TaskRequest:
    description: str
    options: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    priority: int = 5


@dataclass
class TaskPlan:
    task_id: str
    steps: list[dict[str, Any]]
    estimated_duration_sec: float
    risk_level: str  # low | medium | high | critical
    requires_confirmation: bool


@dataclass
class TaskRecord:
    request: TaskRequest
    status: TaskStatus = TaskStatus.QUEUED
    plan: TaskPlan | None = None
    results: list[ActionResult] = field(default_factory=list)
    error: str | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    reasoning_trace: list[str] = field(default_factory=list)
    thinking_state: Any | None = None


# Minimum vision-match confidence below which the agent pauses and asks the
# user to confirm the target before acting on it.
CONFIDENCE_THRESHOLD = 0.90


class SupervisorAgent(BaseAgent):
    """
    Coordinates all agents to complete a user task.

    Pipeline per task:
      1. SecurityAgent -- permission check
      2. PlanningAgent -- break task into steps
      3. [await user confirmation if risk=high]
      4. For each step → dispatch to appropriate agent
      5. ValidationAgent -- verify result
      6. MemoryAgent -- store outcome
      7. Emit completion event
    """

    def __init__(self, ctx: AgentContext) -> None:
        super().__init__(ctx)
        self._queue: asyncio.PriorityQueue[tuple[int, TaskRequest]] = asyncio.PriorityQueue()
        self._active: dict[str, TaskRecord] = {}
        self._history: list[TaskRecord] = []
        self._semaphore = asyncio.Semaphore(3)  # max 3 concurrent tasks
        # Confirmation state -- initialized here to avoid lazy getattr creation
        # and guarantee cleanup even when tasks are externally cancelled.
        self._pending_confirmations: dict[str, asyncio.Event] = {}
        self._pending_element_confirmations: dict[str, asyncio.Event] = {}
        self._pending_element_results: dict[str, list] = {}
        self._ui_lock = asyncio.Lock()  # Exclusivity lock for physical interaction
        self._thinking = get_thinking_engine()
        self._pixel_analyzer = None  # lazily created once, reused across tasks
        self._tool_state = ToolStateManager()
        self._tool_registry = ToolRegistry.default()
        self._tool_executor = ToolExecutor(
            self._tool_registry,
            logger=ToolLogger(ctx.bus, ctx.audit),
            state=self._tool_state,
        )

    @property
    def name(self) -> str:
        return "supervisor"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [AgentCapability("orchestrate", "Coordinates multi-agent task execution")]

    def _subscribe(self) -> None:
        super()._subscribe()
        self.bus.subscribe("task.cancel", self._on_cancel)
        self.bus.subscribe("agent.step_complete", self._on_step_complete)

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    async def submit_task(self, request: TaskRequest) -> str:
        # Validate and sanitize the description before anything else.
        try:
            request.description = validate_task_description(request.description)
        except TaskValidationError as exc:
            log.warning("supervisor.task_rejected", reason=str(exc))
            raise

        await self._queue.put((request.priority, request))
        log.info("supervisor.task_queued", task_id=request.id, description=request.description[:80])
        await self.emit("task.queued", {"task_id": request.id, "description": request.description})
        return request.id

    async def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        if task_id in self._active:
            rec = self._active[task_id]
            return {
                "id": task_id,
                "status": rec.status.name,
                "steps_completed": len(rec.results),
                "steps_total": len(rec.plan.steps) if rec.plan else 0,
                "reasoning": rec.reasoning_trace,
                "thinking_core": rec.thinking_state.public_summary()
                if rec.thinking_state
                else None,
                "error": rec.error,
            }
        for rec in self._history:
            if rec.request.id == task_id:
                return {"id": task_id, "status": rec.status.name, "error": rec.error}
        return None

    # ──────────────────────────────────────────────────────────────
    # Main loop
    # ──────────────────────────────────────────────────────────────

    def tool_diagnostics(self) -> dict[str, Any]:
        """Return a compact snapshot for the tools debug UI and doctor endpoint."""
        return {
            "catalog": self._tool_registry.snapshot(),
            "runtime": self._tool_state.snapshot(),
            "input_monitor": self._tool_executor.input_monitor.status,
            "queue": {"pending": self._tool_executor.queue.pending},
        }

    async def _tick(self) -> None:
        try:
            _, request = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        asyncio.create_task(self._execute_task(request), name=f"task-{request.id}")

    async def _execute_task(self, request: TaskRequest) -> None:
        async with self._semaphore:
            # We also acquire the UI lock for the duration of the execution phase
            # if the task is likely to interact with the screen.
            async with self.task_context(request.id) as profiler:
                record = TaskRecord(request=request)
                self._active[request.id] = record
                t_start = time.monotonic()

                try:
                    with profiler.span("thinking"):
                        await self._phase_thinking(record)
                    with profiler.span("security"):
                        await self._phase_security(record)
                    with profiler.span("planning"):
                        await self._phase_plan(record)
                    with profiler.span("execution"):
                        await self._phase_execute(record, profiler=profiler)
                    with profiler.span("validation"):
                        await self._phase_validate(record)
                    with profiler.span("memorization"):
                        await self._phase_memorize(record)

                    record.status = TaskStatus.COMPLETED
                    record.end_time = time.monotonic()
                    elapsed = record.end_time - t_start

                    await self.emit(
                        "task.completed",
                        {
                            "task_id": request.id,
                            "duration_sec": elapsed,
                            "steps": len(record.results),
                            "result_summary": self._public_result_summary(record),
                        },
                        priority=1,
                    )
                    log.info("supervisor.task_done", duration=f"{elapsed:.2f}s")

                except Exception as exc:
                    record.status = TaskStatus.FAILED
                    record.error = str(exc)
                    record.end_time = time.monotonic()
                    log.error("supervisor.task_failed", error=str(exc))
                    await self.emit(
                        "task.failed", {"task_id": request.id, "error": str(exc)}, priority=1
                    )

                finally:
                    self._history.append(record)
                    self._active.pop(request.id, None)
                    # Trim history to prevent unbounded memory growth.
                    if len(self._history) > 500:
                        self._history = self._history[-500:]

    # ──────────────────────────────────────────────────────────────
    # Phases
    # ──────────────────────────────────────────────────────────────

    async def _phase_security(self, record: TaskRecord) -> None:
        record.status = TaskStatus.PLANNING
        record.reasoning_trace.append("Checking security permissions...")
        security_agent = self._get_agent("security")
        if security_agent:
            allowed = await security_agent.check_task(record.request)
            if not allowed:
                raise PermissionError(
                    f"Task '{record.request.description}' denied by security policy"
                )
        record.reasoning_trace.append("Security check passed.")

    async def _phase_thinking(self, record: TaskRecord) -> None:
        """Build private Thinking Core state before any external action."""
        memory_agent = self._get_agent("memory")
        state = await self._thinking.prepare(
            task_id=record.request.id,
            user_input=record.request.description,
            context={
                "memory_agent": memory_agent,
                "stm": self.ctx.stm,
                "options": record.request.options,
            },
        )
        record.thinking_state = state
        summary = state.public_summary()
        record.reasoning_trace.append(
            "Thinking Core: "
            f"intent={summary['intent']}, depth={summary['reasoning_depth']}, "
            f"confidence={summary['confidence_score']:.2f}, risk={summary['risk_score']:.2f}"
        )
        await self.emit(
            "task.thinking_ready",
            {
                "task_id": record.request.id,
                "intent": summary["intent"],
                "reasoning_depth": summary["reasoning_depth"],
                "confidence_score": summary["confidence_score"],
                "uncertainty_score": summary.get("uncertainty_score"),
                "risk_score": summary["risk_score"],
                "planning_depth": summary.get("planning_depth"),
                "recovery_strategies": summary.get("recovery_strategies", []),
                "checklist_score": summary["checklist_score"],
                "checklist": summary["checklist"],
            },
            priority=4,
        )

    async def _phase_plan(self, record: TaskRecord) -> None:
        record.reasoning_trace.append("Planning task steps...")
        planning_agent = self._get_agent("planning")
        if planning_agent:
            plan = await planning_agent.plan(record.request)
            plan.steps = self._thinking.attach_plan(record.thinking_state, plan.steps)
            record.plan = plan
            record.reasoning_trace.append(f"Plan: {len(plan.steps)} steps, risk={plan.risk_level}")

            if plan.requires_confirmation or plan.risk_level in ("high", "critical"):
                await self.emit(
                    "task.needs_confirmation",
                    {
                        "task_id": record.request.id,
                        "plan": {
                            "steps": plan.steps,
                            "risk_level": plan.risk_level,
                            "estimated_sec": plan.estimated_duration_sec,
                        },
                    },
                    priority=1,
                )
                confirmed = await self._wait_for_confirmation(record.request.id, timeout=60)
                if not confirmed:
                    raise RuntimeError("Task cancelled by user")
        else:
            record.plan = TaskPlan(
                task_id=record.request.id,
                steps=[{"type": "raw", "description": record.request.description}],
                estimated_duration_sec=10.0,
                risk_level="low",
                requires_confirmation=False,
            )

    async def _phase_execute(self, record: TaskRecord, profiler: Any = None) -> None:
        if not record.plan:
            return
        record.status = TaskStatus.EXECUTING
        ui_agent = self._get_agent("ui_automation")
        security_agent = self._get_agent("security")

        if self._pixel_analyzer is None:
            from core.pixel_engine.pixel_analyzer import PixelAnalyzer

            self._pixel_analyzer = PixelAnalyzer()
        pixel_analyzer = self._pixel_analyzer
        held_ui_lock = False
        self._grant_explicit_task_permissions(record)

        # App-fencing: extract scope from plan metadata
        scope_apps: list[str] = (
            record.plan.steps[0].get("scope_apps", []) if record.plan.steps else []
        )
        if not scope_apps and record.request.options.get("scope_apps"):
            scope_apps = record.request.options["scope_apps"]
        if security_agent and scope_apps:
            security_agent.set_task_scope(scope_apps)
            record.reasoning_trace.append(f"App scope: {scope_apps}")

        try:
            for i, step in enumerate(record.plan.steps):
                if self._panic:
                    raise RuntimeError("Emergency stop")

                desc = step.get("description", "Executing action")
                await self.log_terminal(f"Paso {i + 1}/{len(record.plan.steps)}: {desc}", "info")
                record.reasoning_trace.append(f"Step {i + 1}/{len(record.plan.steps)}: {desc}")

                confirmed = bool(record.plan.requires_confirmation or step.get("_confirmed"))
                decision = self._thinking.decide_step(
                    record.thinking_state,
                    step,
                    confirmed=confirmed,
                )
                step = decision.normalized_step
                step["_task_id"] = record.request.id
                step["_session_id"] = record.request.options.get("session_id")
                if not decision.allowed:
                    result = ActionResult(
                        success=False,
                        error=decision.reason,
                        confidence=0.0,
                        reasoning="Thinking Core rejected unsafe or invalid tool step",
                    )
                    record.results.append(result)
                    if step.get("critical", False):
                        raise RuntimeError(f"Critical step {i + 1} rejected: {decision.reason}")
                    record.reasoning_trace.append(
                        f"Step {i + 1} rejected by Thinking Core: {decision.reason}"
                    )
                    continue

                await self._emit_htlgg_decision(record, step, confirmed)

                physical_action = self._is_physical_step(step)
                if physical_action:
                    await self._ui_lock.acquire()
                    held_ui_lock = True

                # Reactive Vision: force a scan before action if the last one is stale
                await self.ctx.capture.capture()

                await self.emit(
                    "task.step_started",
                    {
                        "task_id": record.request.id,
                        "step": i + 1,
                        "total": len(record.plan.steps),
                        "description": step.get("description", ""),
                    },
                )

                # App-fencing: block if focused window is out of scope
                if security_agent and scope_apps:
                    if physical_action:
                        if not await security_agent.check_action_window():
                            record.reasoning_trace.append(
                                f"Step {i + 1} skipped -- focused window outside task scope."
                            )
                            await asyncio.sleep(0.5)
                            if held_ui_lock:
                                self._release_ui_lock()
                                held_ui_lock = False
                            continue

                # HUD: announce the action the agent is about to take
                if step.get("type") in ("click", "type") and step.get("target"):
                    await self._hud_announce(step, record.request.id)

                # Learning mode: pre-flight confidence check for click actions
                if step.get("type") == "click" and step.get("target"):
                    vision_agent = self._get_agent("vision")
                    if vision_agent:
                        step = await self._learning_mode_check(step, vision_agent, record)

                # Capture baseline frame for self-healing diff
                frame_before: Any | None = None
                if step.get("type") in ("click", "type", "key", "navigate", "scroll"):
                    frame_before = await self._capture_frame()

                step_label = f"step.{step.get('type', 'unknown')}"
                if profiler:
                    with profiler.span(step_label):
                        result = await self._dispatch_step_with_retry(step, ui_agent, record)
                else:
                    result = await self._dispatch_step_with_retry(step, ui_agent, record)

                # Self-healing: if action succeeded but produced no visual change, auto-correct.
                if result.success and frame_before is not None:
                    result = await self._verify_and_heal(
                        step,
                        result,
                        frame_before,
                        pixel_analyzer,
                        ui_agent,
                        record,
                    )

                # On confirmed successful click: save template to visual memory
                if result.success and step.get("type") == "click":
                    await self._maybe_save_template(step, record)

                record.results.append(result)
                self._thinking.observe_step_result(
                    record.thinking_state,
                    step=step,
                    success=result.success,
                    error=result.error,
                )

                if not result.success:
                    await self.emit(
                        "task.execution_reflection",
                        {
                            "task_id": record.request.id,
                            "step": i + 1,
                            "step_type": step.get("type"),
                            "error": result.error,
                            "thinking_core": record.thinking_state.public_summary()
                            if record.thinking_state
                            else None,
                        },
                    )
                    for fallback in self._thinking.recovery_steps(
                        record.thinking_state,
                        step,
                        error=result.error,
                    ):
                        fallback["_task_id"] = record.request.id
                        fallback["_session_id"] = step.get("_session_id")
                        fallback_result = await self._dispatch_step(fallback, ui_agent)
                        record.results.append(fallback_result)
                        self._thinking.observe_step_result(
                            record.thinking_state,
                            step=fallback,
                            success=fallback_result.success,
                            error=fallback_result.error,
                        )
                        if fallback_result.success and fallback.get(
                            "equivalent_to_original", False
                        ):
                            result = fallback_result
                            record.reasoning_trace.append(
                                f"Step {i + 1} recovered with fallback {fallback.get('type')}"
                            )
                            break
                        if fallback_result.success:
                            record.reasoning_trace.append(
                                f"Step {i + 1} fallback {fallback.get('type')} "
                                "captured diagnostic evidence; original action remains failed."
                            )
                    if result.success:
                        await self.emit("hud.clear", {})
                        await asyncio.sleep(0.05)
                        if held_ui_lock:
                            self._release_ui_lock()
                            held_ui_lock = False
                        continue
                    if step.get("critical", False):
                        raise RuntimeError(f"Critical step {i + 1} failed: {result.error}")
                    record.reasoning_trace.append(f"Step {i + 1} non-critical failure, continuing.")

                # Clear HUD annotation after action
                await self.emit("hud.clear", {})

                await asyncio.sleep(0.05)
                if held_ui_lock:
                    self._release_ui_lock()
                    held_ui_lock = False

        finally:
            if held_ui_lock:
                self._release_ui_lock()
            self.ctx.security.revoke_scope(task_id=record.request.id)
            # Always lift app scope restrictions when phase ends
            if security_agent:
                security_agent.clear_task_scope()

    async def _dispatch_step_with_retry(
        self,
        step: dict,
        ui_agent: Any,
        record: TaskRecord,
    ) -> ActionResult:
        retry_policy = (
            step.get("retry_policy") if isinstance(step.get("retry_policy"), dict) else {}
        )
        max_attempts = max(1, min(int(retry_policy.get("max_attempts", 1) or 1), 3))
        backoff_ms = max(0, min(int(retry_policy.get("backoff_ms", 0) or 0), 5_000))
        result = ActionResult(success=False, error="not executed")

        for attempt in range(1, max_attempts + 1):
            result = await self._dispatch_step(step, ui_agent)
            if result.success or attempt >= max_attempts:
                return result

            delay = (backoff_ms / 1000.0) * attempt
            record.reasoning_trace.append(
                f"Retry {attempt}/{max_attempts - 1} for {step.get('type', 'unknown')}: {result.error}"
            )
            await self.emit(
                "task.step_retrying",
                {
                    "task_id": record.request.id,
                    "step_type": step.get("type"),
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "error": result.error,
                },
            )
            await self.log_terminal(
                f"Reintento {attempt + 1}/{max_attempts}: {step.get('description', step.get('type', 'step'))}",
                "info",
            )
            if delay:
                await asyncio.sleep(delay)

        return result

    async def _dispatch_step(self, step: dict, ui_agent: Any) -> ActionResult:
        tool_result = await self._tool_executor.execute_step(
            step,
            task_id=step.get("task_id") or step.get("_task_id") or None,
            runtime=self.ctx.runtime,
            agent_pool=self.ctx.runtime._agent_pool,
            capture=self.ctx.capture,
            accessibility=self.ctx.accessibility,
            security=self.ctx.security,
            audit=self.ctx.audit,
            bus=self.ctx.bus,
            approved_risk=bool(step.get("_confirmed") or step.get("approved_risk")),
        )
        action = ActionResult(
            success=tool_result.success,
            data=tool_result.output,
            error=tool_result.error,
            error_code=tool_result.error_code,
            confidence=tool_result.confidence,
            reasoning=(
                f"Tool {tool_result.tool} "
                f"{'completed' if tool_result.success else 'failed'}"
                + (f" via {tool_result.fallback_used}" if tool_result.fallback_used else "")
            ),
            duration_ms=tool_result.duration_ms,
        )
        self._track_action(action.duration_ms, action.success)
        return action

    def _public_result_summary(self, record: TaskRecord) -> str:
        """Build a compact visible outcome summary without private reasoning."""
        parts: list[str] = []
        for result in record.results[-5:]:
            if not result.success:
                continue
            data = result.data if isinstance(result.data, dict) else {}
            if isinstance(data.get("results"), list):
                for item in data["results"][:5]:
                    title = str(item.get("title", "")).strip()
                    url = str(item.get("url", "")).strip()
                    snippet = str(item.get("snippet", "")).strip()
                    line = " - ".join(part for part in (title, url, snippet) if part)
                    if line:
                        parts.append(line)
            elif data.get("content"):
                parts.append(str(data["content"]).strip())
            elif data.get("stdout"):
                parts.append(str(data["stdout"]).strip())
            elif data.get("stderr"):
                parts.append(str(data["stderr"]).strip())
            elif result.reasoning:
                parts.append(result.reasoning)

        summary = "\n".join(part for part in parts if part).strip()
        if not summary:
            summary = "Tarea completada y validada por el supervisor."
        return summary[:2000]

    async def _phase_validate(self, record: TaskRecord) -> None:
        record.status = TaskStatus.VALIDATING
        record.reasoning_trace.append("Validating task outcome...")
        failed_results = [result for result in record.results if not result.success]
        if failed_results:
            latest = failed_results[-1]
            code = latest.error_code or "tool.action_failed"
            raise RuntimeError(f"Task contains a failed action ({code}): {latest.error}")
        validation_agent = self._get_agent("validation")
        if validation_agent and record.plan:
            ok = await validation_agent.validate_task(record)
            if not ok:
                raise RuntimeError("Task validation failed")

    async def _phase_memorize(self, record: TaskRecord) -> None:
        self._thinking.mark_memory_updated(record.thinking_state)
        memory_agent = self._get_agent("memory")
        if memory_agent:
            await memory_agent.store_task_outcome(record)

    # ──────────────────────────────────────────────────────────────
    # HUD / Learning-mode / Template helpers
    # ──────────────────────────────────────────────────────────────

    async def _hud_announce(self, step: dict, task_id: str) -> None:
        """Emit a HUD draw event + trust dashboard action_preview."""
        bounds = step.get("bounds")
        if bounds:
            cx = bounds[0] + bounds[2] // 2
            cy = bounds[1] + bounds[3] // 2
        else:
            cx, cy = -1, -1

        label = step.get("description", step.get("target", ""))

        await self.emit(
            "hud.draw",
            {
                "type": "circle",
                "x": cx,
                "y": cy,
                "label": label,
                "color": "#ff3a3a",
                "task_id": task_id,
            },
        )

        # Trust dashboard: mark the action target in red
        await self.emit(
            "task.action_preview",
            {
                "step_type": step.get("type"),
                "target": step.get("target", ""),
                "bounds": bounds,
                "label": label,
                "task_id": task_id,
            },
        )

    async def _learning_mode_check(
        self,
        step: dict,
        vision_agent: Any,
        record: TaskRecord,
    ) -> dict:
        """
        If the element the agent found has low confidence (< 0.6), pause and
        ask the user to confirm or correct it before proceeding.
        """
        target = step.get("target", "")
        el = await vision_agent.find_element(target)

        if el is None:
            await self.emit(
                "vision.element_uncertain",
                {
                    "task_id": record.request.id,
                    "target": target,
                    "bounds": None,
                    "confidence": 0.0,
                    "question": f"Cannot safely locate '{target}'.",
                },
                priority=1,
            )
            return {**step, "confidence": 0.0}

        step = {
            **step,
            "confidence": el.confidence,
            "bounds": list(el.bounds),
            "element_id": el.id,
            "element_source": el.source,
        }
        if el.confidence >= CONFIDENCE_THRESHOLD:
            return step
        if el.confidence >= 0.50:
            record.reasoning_trace.append(
                f"Vision confidence {el.confidence:.2f}; executor will pre-verify or re-observe."
            )
            return step

        log.warning("supervisor.learning_mode_triggered", target=target, confidence=el.confidence)

        # Show HUD highlight on the low-confidence element
        if el.bounds:
            cx = el.bounds[0] + el.bounds[2] // 2
            cy = el.bounds[1] + el.bounds[3] // 2
            await self.emit(
                "hud.draw",
                {
                    "type": "circle",
                    "x": cx,
                    "y": cy,
                    "label": f"? {target}",
                    "color": "#ffcc00",
                    "task_id": record.request.id,
                },
            )

        await self.emit(
            "vision.element_uncertain",
            {
                "task_id": record.request.id,
                "target": target,
                "bounds": list(el.bounds),
                "confidence": el.confidence,
                "question": f"Is this the correct element for '{target}'?",
            },
            priority=1,
        )

        confirmed = await self._wait_for_element_confirmation(record.request.id, timeout=30)

        if confirmed is True:
            # User approved -- save template so we never ask again
            record.reasoning_trace.append(
                f"Learning: user confirmed '{target}' (conf={el.confidence:.2f}) -- saving template."
            )
            step = {**step, "bounds": list(el.bounds), "_confirmed": True, "confidence": 1.0}

        elif isinstance(confirmed, list) and len(confirmed) == 4:
            # User provided corrected bounds
            record.reasoning_trace.append(
                f"Learning: user corrected '{target}' bounds -- saving corrected template."
            )
            step = {
                **step,
                "bounds": confirmed,
                "_confirmed": True,
                "_corrected": True,
                "confidence": 1.0,
            }

        return step

    async def _maybe_save_template(self, step: dict, record: TaskRecord) -> None:
        """After a successful click, persist a template crop to VisualMemory."""
        bounds = step.get("bounds")
        if not bounds or not step.get("_confirmed") and step.get("type") != "click":
            return
        try:
            frame = await self._capture_frame()
            if frame is None:
                return
            active_win = ""
            try:
                active_win = await self.ctx.accessibility.get_active_window_title() or ""
            except Exception:
                pass
            crop = self.ctx.visual_memory.crop_from_screenshot(frame, tuple(bounds))
            label = step.get("target", step.get("description", "unknown"))
            self.ctx.visual_memory.save_template(active_win, label, crop)
        except Exception as exc:
            log.debug("supervisor.template_save_failed", error=str(exc))

    async def _wait_for_element_confirmation(
        self, task_id: str, timeout: float
    ) -> bool | list | None:
        """Wait for user to confirm or correct an uncertain element.

        Returns:
            True   -- user confirmed the element is correct
            list   -- user provided corrected [x, y, w, h] bounds
            None   -- timeout or task cancelled
        """
        ev: asyncio.Event = asyncio.Event()
        result_holder: list = [None]

        self._pending_element_confirmations[task_id] = ev
        self._pending_element_results[task_id] = result_holder

        try:
            await asyncio.wait_for(asyncio.shield(ev.wait()), timeout=timeout)
            return result_holder[0]
        except (TimeoutError, asyncio.CancelledError):
            return None
        finally:
            # Always clean up -- even on external cancellation
            self._pending_element_confirmations.pop(task_id, None)
            self._pending_element_results.pop(task_id, None)

    async def confirm_element(
        self, task_id: str, correct: bool, corrected_bounds: list | None = None
    ) -> None:
        """Called by IPC when user answers the element confirmation prompt."""
        ev = getattr(self, "_pending_element_confirmations", {}).get(task_id)
        holder = getattr(self, "_pending_element_results", {}).get(task_id)
        if ev and holder is not None:
            holder[0] = corrected_bounds if (not correct and corrected_bounds) else correct
            ev.set()

    # ──────────────────────────────────────────────────────────────
    # Self-healing helpers
    # ──────────────────────────────────────────────────────────────

    async def _capture_frame(self) -> Any | None:
        """Grab a frame through the Rust/Tauri vision bridge only."""
        try:
            return await self.ctx.capture.capture()
        except Exception as exc:
            log.debug("supervisor.capture_unavailable", error=str(exc))
            return None

    async def _verify_and_heal(
        self,
        step: dict,
        result: ActionResult,
        frame_before: Any,
        pixel_analyzer: Any,
        ui_agent: Any,
        record: TaskRecord,
    ) -> ActionResult:
        """
        Compare screen before/after an action.  If no visual change is detected
        (changed_ratio < VISUAL_EFFECT_THRESHOLD) the action is assumed to have
        missed its target and we attempt auto-correction without user intervention.
        """
        VISUAL_EFFECT_THRESHOLD = 0.005  # <0.5 % pixel change = probably missed
        SETTLE_S = 0.30  # wait for animations to start

        await asyncio.sleep(SETTLE_S)
        frame_after = await self._capture_frame()
        if frame_after is None:
            return result

        # Feed both frames into the analyzer; the second call returns the diff.
        pixel_analyzer.process_frame(frame_before)
        diff = pixel_analyzer.process_frame(frame_after)

        if diff is None or diff.changed_ratio >= VISUAL_EFFECT_THRESHOLD:
            return result  # visible change confirmed -- action worked

        step_type = step.get("type", "")
        log.warning(
            "supervisor.self_heal_triggered",
            step_type=step_type,
            changed_ratio=f"{diff.changed_ratio:.4f}",
        )
        record.reasoning_trace.append(
            f"Self-heal: '{step_type}' had no visual effect (Δ={diff.changed_ratio:.4f}), retrying…"
        )
        await self.emit(
            "task.self_healing",
            {
                "task_id": record.request.id,
                "step_type": step_type,
                "changed_ratio": diff.changed_ratio,
            },
        )

        # Attempt 1 -- retry the exact same step
        healed = await self._dispatch_step(step, ui_agent)
        if healed.success:
            await asyncio.sleep(SETTLE_S)
            frame_retry = await self._capture_frame()
            if frame_retry is not None:
                pixel_analyzer.process_frame(frame_after)
                diff2 = pixel_analyzer.process_frame(frame_retry)
                if diff2 and diff2.changed_ratio >= VISUAL_EFFECT_THRESHOLD:
                    record.reasoning_trace.append("Self-heal: retry confirmed by visual diff.")
                    return healed

        # Attempt 2 -- scroll to expose the target, then retry
        if step_type in ("click", "type") and step.get("target"):
            try:
                await self._dispatch_step(
                    {"type": "scroll", "direction": "down", "amount": 3},
                    ui_agent,
                )
                await asyncio.sleep(0.2)
                frame_scrolled = await self._capture_frame()
                healed2 = await self._dispatch_step(step, ui_agent)
                record.reasoning_trace.append("Self-heal: scroll+retry executed.")
                if healed2.success and frame_scrolled is not None:
                    await asyncio.sleep(SETTLE_S)
                    frame_healed = await self._capture_frame()
                    if frame_healed is not None:
                        pixel_analyzer.process_frame(frame_scrolled)
                        diff3 = pixel_analyzer.process_frame(frame_healed)
                        if diff3 and diff3.changed_ratio >= VISUAL_EFFECT_THRESHOLD:
                            record.reasoning_trace.append(
                                "Self-heal: scroll+retry confirmed by visual diff."
                            )
                            return healed2
            except Exception:
                pass

        record.reasoning_trace.append("Self-heal: could not confirm visual effect after retries.")
        return ActionResult(
            success=False,
            error="Action produced no confirmed visual change after recovery attempts",
            error_code="tool.verification_failed",
            confidence=0.0,
            reasoning="Visual verification failed",
            data={"original_result": result.data},
        )

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _get_agent(self, name: str) -> Any:
        return self.ctx.runtime._agent_pool.get(name)

    @staticmethod
    def _is_physical_step(step: dict[str, Any]) -> bool:
        return str(step.get("type") or step.get("tool_id") or "").lower() in {
            "click",
            "mouse_click",
            "double_click",
            "right_click",
            "move_mouse",
            "drag",
            "type",
            "type_text",
            "key",
            "hotkey",
            "scroll",
            "navigate",
            "close_app",
            "computer",
            "mouse.click",
            "mouse.double_click",
            "mouse.right_click",
            "mouse.move",
            "mouse.drag",
            "mouse.scroll",
            "keyboard.type_text",
            "keyboard.hotkey",
            "app.open",
            "app.close",
        }

    def _release_ui_lock(self) -> None:
        if self._ui_lock.locked():
            self._ui_lock.release()

    def _grant_explicit_task_permissions(self, record: TaskRecord) -> None:
        mapping = {
            "screen": "SCREEN_READ",
            "screen_read": "SCREEN_READ",
            "mouse": "INPUT_MOUSE",
            "keyboard": "INPUT_KEYBOARD",
            "process": "PROCESS_LAUNCH",
            "file_read": "FILE_READ",
            "file_write": "FILE_WRITE",
            "network": "NETWORK",
        }
        requested = record.request.options.get("permissions", [])
        if not isinstance(requested, list):
            return
        ttl_sec = min(
            3600.0,
            max(1.0, float(record.request.options.get("permission_ttl_sec", 900.0))),
        )
        for name in requested:
            permission = mapping.get(str(name).strip().lower())
            if permission:
                self.ctx.security.grant(
                    permission,
                    task_id=record.request.id,
                    session_id=record.request.options.get("session_id"),
                    ttl_sec=ttl_sec,
                )

    async def _emit_htlgg_decision(
        self,
        record: TaskRecord,
        step: dict[str, Any],
        confirmed: bool,
    ) -> None:
        htlgg = getattr(self.ctx.runtime, "htlgg", None)
        if htlgg is None:
            return
        risk_name = str(
            step.get("risk_level")
            or (record.plan.risk_level if record.plan else "low")
        ).lower()
        risk = {
            "low": RiskLevel.R0,
            "medium": RiskLevel.R1,
            "high": RiskLevel.R2,
            "critical": RiskLevel.R3,
        }.get(risk_name, RiskLevel.R1)
        await htlgg.emit(
            HtlggEnvelope(
                record=DecisionRecord(
                    action=str(step.get("tool_id") or step.get("type") or "unknown"),
                    element_id=str(step.get("element_id") or ""),
                    expected=str(step.get("expected_outcome") or ""),
                    risk=risk,
                    confirmed=confirmed,
                ),
                session_id=str(record.request.options.get("session_id") or record.request.id),
                task_id=record.request.id,
                metadata={"confidence": step.get("confidence")},
            )
        )

    async def _wait_for_confirmation(self, task_id: str, timeout: float) -> bool:
        confirmed_event = asyncio.Event()
        self._pending_confirmations[task_id] = confirmed_event

        try:
            await asyncio.wait_for(asyncio.shield(confirmed_event.wait()), timeout=timeout)
            return True
        except (TimeoutError, asyncio.CancelledError):
            return False
        finally:
            # Always clean up -- even on external cancellation
            self._pending_confirmations.pop(task_id, None)

    async def confirm_task(self, task_id: str) -> None:
        confirmations = getattr(self, "_pending_confirmations", {})
        if task_id in confirmations:
            confirmations[task_id].set()

    async def _on_cancel(self, event: Event) -> None:
        task_id = event.payload.get("task_id")
        if task_id in self._active:
            self._active[task_id].status = TaskStatus.CANCELLED

    async def _on_step_complete(self, event: Event) -> None:
        pass
