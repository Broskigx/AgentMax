"""Validated execution engine for AgentMax tools."""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from core.config import get_config
from core.feature_flags import FeatureFlags, disabled_features
from core.htlgg import (
    DecisionRecord,
    ExecutionOutcome,
    ExecutionRecord,
    HtlggEnvelope,
    RiskLevel,
)
from core.security.policy import SecurityPolicy
from core.tools.context_bridge import ToolContextBridge
from core.tools.fallback import ToolFallbackManager
from core.tools.input_monitor import UserInputMonitor
from core.tools.logger import ToolLogger
from core.tools.models import (
    ToolDefinition,
    ToolExecutionContext,
    ToolRequest,
    ToolResult,
    ToolThinkingRecord,
)
from core.tools.normalizer import ToolResultNormalizer
from core.tools.permissions import ToolPermissionManager
from core.tools.queue import ToolQueue
from core.tools.registry import ToolRegistry
from core.tools.risk import ToolRiskAnalyzer
from core.tools.router import ToolRouter
from core.tools.state import ToolStateManager
from core.tools.validator import ToolValidator

if TYPE_CHECKING:
    from core.a2a.manager import A2AManager
    from core.mcp.manager import MCPManager

log = structlog.get_logger(__name__)


class ToolExecutor:
    """Executes one tool through the full validation, safety and retry pipeline."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        validator: ToolValidator | None = None,
        permissions: ToolPermissionManager | None = None,
        risk: ToolRiskAnalyzer | None = None,
        queue: ToolQueue | None = None,
        logger: ToolLogger | None = None,
        state: ToolStateManager | None = None,
        context_bridge: ToolContextBridge | None = None,
        fallback: ToolFallbackManager | None = None,
        input_monitor: UserInputMonitor | None = None,
        security_policy: SecurityPolicy | None = None,
        mcp_manager: MCPManager | None = None,
        a2a_manager: A2AManager | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry.default()
        self.mcp_manager = mcp_manager
        self.a2a_manager = a2a_manager
        self.validator = validator or ToolValidator()
        self.permissions = permissions or ToolPermissionManager()
        self.risk = risk or ToolRiskAnalyzer()
        self.queue = queue or ToolQueue()
        self.logger = logger or ToolLogger()
        self.state = state or ToolStateManager()
        self.context_bridge = context_bridge or ToolContextBridge()
        self.fallback = fallback or ToolFallbackManager()
        self.input_monitor = input_monitor or UserInputMonitor()
        configured_dirs = getattr(get_config().security, "allowed_fs_dirs", [])
        self.security_policy = security_policy or SecurityPolicy(configured_dirs)
        self._physical_lock = asyncio.Lock()
        self.router = ToolRouter()
        self.normalizer = ToolResultNormalizer()

    async def execute_step(
        self,
        step: dict[str, Any],
        *,
        task_id: str | None = None,
        runtime: Any = None,
        agent_pool: dict[str, Any] | None = None,
        capture: Any = None,
        accessibility: Any = None,
        security: Any = None,
        audit: Any = None,
        bus: Any = None,
        dry_run: bool = False,
        approved_risk: bool = False,
    ) -> ToolResult:
        request = self.router.route_step(step, task_id=task_id)
        request.dry_run = request.dry_run or dry_run
        request.approved_risk = request.approved_risk or approved_risk
        context = await self.context_bridge.build(
            request=request,
            runtime=runtime,
            agent_pool=agent_pool,
            capture=capture,
            accessibility=accessibility,
            security=security,
            audit=audit,
            bus=bus,
            user_idle=not self.input_monitor.pause_controller.paused,
            extra={
                "input_control": True,
                "session_id": step.get("_session_id") or step.get("session_id"),
            },
        )
        if context.bus and self.logger.bus is None:
            self.logger.bus = context.bus
        if context.audit and self.logger.audit is None:
            self.logger.audit = context.audit
        return await self.execute(request, context)

    async def execute(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        definition = self.registry.maybe_get(request.tool_id)
        if not definition:
            result = self.normalizer.failure(
                request, "tool.unknown", f"Unknown tool: {request.tool_id}"
            )
            await self.logger.failed(result)
            await self._emit_htlgg_execution(request, context, result)
            return result

        result = await self._execute_with_retries(definition, request, context)
        if not result.success:
            result = await self._try_fallbacks(definition, request, context, result)
        await self._emit_htlgg_execution(request, context, result)
        return result

    async def _execute_with_retries(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
    ) -> ToolResult:
        attempts = definition.retry_policy.max_attempts
        last = self.normalizer.failure(request, "tool.not_executed", "Tool was not executed")
        for attempt in range(1, attempts + 1):
            last = await self._execute_once(definition, request, context, attempt=attempt)
            last.attempts = attempt
            if last.success:
                return last
            if attempt < attempts:
                await asyncio.sleep((definition.retry_policy.backoff_ms / 1000.0) * attempt)
        return last

    async def _execute_once(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
        *,
        attempt: int,
    ) -> ToolResult:
        if self._is_physical_definition(definition):
            async with self._physical_lock:
                if request.dry_run:
                    return await self._execute_once_locked(
                        definition,
                        request,
                        context,
                        attempt=attempt,
                    )
                self.input_monitor.configure(
                    bus=context.bus,
                    authorization_validator=lambda _task_id: self.permissions.validate(
                        definition, request, context
                    ).ok,
                )
                self.input_monitor.start_task(request.task_id)
                try:
                    if self.input_monitor.pause_controller.paused:
                        await self.input_monitor.wait_until_safe()
                    context.user_idle = True
                    return await self._execute_once_locked(
                        definition,
                        request,
                        context,
                        attempt=attempt,
                    )
                except PermissionError as exc:
                    return self.normalizer.failure(
                        request, "tool.permission_required", str(exc)
                    )
                finally:
                    self.input_monitor.stop_task()
        return await self._execute_once_locked(
            definition,
            request,
            context,
            attempt=attempt,
        )

    async def _execute_once_locked(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
        *,
        attempt: int,
    ) -> ToolResult:
        t0 = time.monotonic()
        can_run, cooldown_reason = self.state.can_run(definition.id)
        if not can_run:
            return self.normalizer.failure(
                request,
                "tool.cooldown",
                cooldown_reason or "Tool cooldown active",
            )

        if not request.dry_run:
            flags = self._feature_flags(context)
            disabled = disabled_features(definition.id, flags, request.input)
            if disabled:
                return self.normalizer.failure(
                    request,
                    "tool.feature_disabled",
                    f"Tool {definition.id} requires disabled feature(s): {', '.join(disabled)}",
                )

        risk_block = self.risk.blocks_execution(definition, request.input)
        if risk_block:
            return self.normalizer.failure(
                request, risk_block, "Tool input was blocked by risk policy"
            )

        risk_report = self.risk.analyze(definition, request.input)
        if (
            risk_report["requires_confirmation"]
            and not request.approved_risk
            and not request.dry_run
        ):
            return self.normalizer.failure(
                request,
                "tool.confirmation_required",
                f"Tool {definition.id} requires confirmation for {risk_report['level']} risk",
            )

        validation = self.validator.validate(definition, request, context)
        if not validation.ok:
            await self.logger.validation_failed(definition, request, validation.error_text())
            return self.normalizer.failure(
                request, "tool.validation_failed", validation.error_text()
            )

        permission = self.permissions.validate(definition, request, context)
        if not permission.ok:
            await self.logger.validation_failed(definition, request, permission.error_text())
            return self.normalizer.failure(
                request, "tool.permission_required", permission.error_text()
            )

        await self._emit_htlgg_decision(request, context, risk_report)

        if not request.dry_run:
            preflight_failure = await self._preflight_verification(definition, request, context)
            if preflight_failure is not None:
                return preflight_failure

        thinking = self._thinking_for(definition, request, risk_report)
        await self.logger.started(definition, request)
        self.state.mark_started(definition.id)

        async def work() -> ToolResult:
            if definition.requires_user_idle and not request.dry_run:
                if self.input_monitor.pause_controller.paused:
                    self.state.mark_paused(definition.id, "paused_by_user")
                    await self.logger.paused(
                        definition.id,
                        request.task_id,
                        "Control pausado por actividad del usuario",
                    )
                    try:
                        await self.input_monitor.wait_until_safe()
                    except PermissionError as exc:
                        return self.normalizer.failure(
                            request, "tool.permission_required", str(exc)
                        )
                    await self.logger.resumed(definition.id, request.task_id)
                context.user_idle = True

            if request.dry_run:
                return self.normalizer.success(
                    request,
                    {"dry_run": True, "would_execute": definition.id},
                    duration_ms=(time.monotonic() - t0) * 1000,
                    confidence=0.95,
                )

            try:
                result = await asyncio.wait_for(
                    self._run_builtin_tool(definition, request, context),
                    timeout=definition.timeout_ms / 1000.0,
                )
            except TimeoutError:
                result = self.normalizer.failure(
                    request,
                    "tool.timeout",
                    f"Tool {definition.id} timed out after {definition.timeout_ms}ms",
                    duration_ms=(time.monotonic() - t0) * 1000,
                )
            result.thinking = thinking
            return result

        result = await self.queue.run(definition.execution_mode.value, work)
        result.duration_ms = result.duration_ms or (time.monotonic() - t0) * 1000
        result.completed_at = time.time()
        result.attempts = attempt
        self.state.mark_result(result, cooldown_ms=definition.cooldown_ms)
        if result.success:
            await self.logger.completed(result)
        else:
            await self.logger.failed(result)
        return result

    async def _emit_htlgg_execution(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        result: ToolResult,
    ) -> None:
        bus = getattr(context.runtime, "htlgg", None)
        if bus is None:
            return
        if result.success:
            outcome = ExecutionOutcome.Y0
        elif result.error_code == "tool.verification_failed":
            outcome = ExecutionOutcome.Y2
        elif result.error_code == "tool.permission_required":
            outcome = ExecutionOutcome.Y3
        elif result.error_code in {"tool.feature_disabled", "tool.confirmation_required"}:
            outcome = ExecutionOutcome.Y4
        elif result.error_code == "tool.timeout":
            outcome = ExecutionOutcome.Y5
        else:
            outcome = ExecutionOutcome.Y1
        x = request.input.get("x")
        y = request.input.get("y")
        await bus.emit(
            HtlggEnvelope(
                record=ExecutionRecord(
                    action=request.tool_id,
                    x=int(x) if isinstance(x, (int, float)) else None,
                    y=int(y) if isinstance(y, (int, float)) else None,
                    outcome=outcome,
                    detail=result.error or "ok",
                ),
                session_id=str(context.extra.get("session_id") or request.task_id or "local"),
                task_id=request.task_id,
                metadata={"attempts": result.attempts, "confidence": result.confidence},
            )
        )

    async def _emit_htlgg_decision(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        risk_report: dict[str, Any],
    ) -> None:
        bus = getattr(context.runtime, "htlgg", None)
        if bus is None:
            return
        risk = {
            "low": RiskLevel.R0,
            "medium": RiskLevel.R1,
            "high": RiskLevel.R2,
            "critical": RiskLevel.R3,
        }.get(str(risk_report.get("level")), RiskLevel.R1)
        await bus.emit(
            HtlggEnvelope(
                record=DecisionRecord(
                    action=request.tool_id,
                    element_id=str(request.metadata.get("element_id") or ""),
                    expected=str(request.metadata.get("expected_outcome") or ""),
                    risk=risk,
                    confirmed=bool(request.approved_risk),
                ),
                session_id=str(context.extra.get("session_id") or request.task_id or "local"),
                task_id=request.task_id,
                metadata={"confidence": request.metadata.get("confidence")},
            )
        )

    async def _try_fallbacks(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
        failed_result: ToolResult,
    ) -> ToolResult:
        if failed_result.error_code in {
            "tool.feature_disabled",
            "tool.permission_required",
            "tool.confirmation_required",
            "tool.verification_failed",
            "critical_risk_blocked",
        }:
            return failed_result
        for fallback_request in self.fallback.build_fallback_requests(
            definition, request, failed_result
        ):
            fallback_definition = self.registry.maybe_get(fallback_request.tool_id)
            if not fallback_definition:
                continue
            result = await self._execute_with_retries(
                fallback_definition, fallback_request, context
            )
            if result.success:
                failed_result.fallback_used = fallback_request.tool_id
                failed_result.output["fallback_observation"] = {
                    "tool": fallback_request.tool_id,
                    "output": result.output,
                }
                failed_result.next_recommended_action = "replan_with_fallback_observation"
                return failed_result
        return failed_result

    @staticmethod
    def _is_physical_definition(definition: ToolDefinition) -> bool:
        return definition.id == "computer.execute" or definition.id.startswith(
            ("mouse.", "keyboard.", "app.", "ui.")
        )

    def _thinking_for(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        risk_report: dict[str, Any],
    ) -> ToolThinkingRecord:
        goal = str(request.metadata.get("description") or request.tool_id)
        return ToolThinkingRecord(
            goal=goal,
            reason_for_tool=f"Tool {definition.id} matches the current step and category {definition.category}.",
            selected_tool=definition.id,
            risk_level=str(risk_report["level"]),
            expected_result=f"{definition.name} completes and returns a normalized result.",
            fallback_if_fails=definition.fallback_chain,
            validation_after_execution=", ".join(
                definition.validation_rules or ["normalized_result"]
            ),
            requires_user_idle=definition.requires_user_idle,
            confidence=max(0.0, min(1.0, definition.reasoning_weight)),
        )

    async def _preflight_verification(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
    ) -> ToolResult | None:
        if not self._is_physical_definition(definition):
            return None

        capture = context.capture
        if capture and hasattr(capture, "detect_animation"):
            animation = await capture.detect_animation()
            if animation in {"spinner", "transition", "typing", "scrolling"}:
                if not hasattr(capture, "wait_for_stable_screen"):
                    return self.normalizer.failure(
                        request,
                        "tool.verification_failed",
                        f"Screen is currently {animation} and no stability check is available",
                    )
                stable = await capture.wait_for_stable_screen()
                if not stable:
                    return self.normalizer.failure(
                        request,
                        "tool.verification_failed",
                        f"Screen did not stabilize from state: {animation}",
                    )

        explicit_confidence = request.metadata.get("confidence", request.input.get("confidence"))
        if explicit_confidence is None:
            has_grounded_coords = (
                ("x" in request.input and "y" in request.input)
                or bool(request.input.get("path"))
                or all(key in request.input for key in ("x1", "y1", "x2", "y2"))
            )
            explicit_confidence = 1.0 if has_grounded_coords else definition.reasoning_weight
        decision = self.risk.verification_policy(
            definition,
            request.input,
            confidence=float(explicit_confidence),
        )

        if decision["action"] == "block":
            return self.normalizer.failure(
                request,
                "tool.confirmation_required",
                "Action confidence is below 0.50; clarification or explicit confirmation is required",
            )
        if decision["action"] == "reobserve":
            if capture and hasattr(capture, "capture"):
                await capture.capture()
            return self.normalizer.failure(
                request,
                "tool.verification_failed",
                "Action confidence is between 0.50 and 0.69; re-observation is required",
            )
        if decision["action"] != "pre_verify":
            return None

        target = request.input.get("target")
        if definition.id.startswith(("mouse.", "app.", "ui.")):
            target = target or request.input.get("text")
        if not target:
            if capture and hasattr(capture, "capture"):
                frame = await capture.capture()
                if frame:
                    return None
            return self.normalizer.failure(
                request,
                "tool.verification_failed",
                "Reinforced pre-verification requires a current screen observation",
            )
        if isinstance(target, dict):
            target = target.get("text") or target.get("description")
        if not target:
            return None

        vision = context.agent_pool.get("vision")
        if not vision or not hasattr(vision, "find_element"):
            return self.normalizer.failure(
                request,
                "tool.verification_failed",
                "A visual target requires an available element locator",
            )
        element = await vision.find_element(str(target))
        bounds = getattr(element, "bounds", None) if element else None
        confidence = float(getattr(element, "confidence", 0.0)) if element else 0.0
        if not bounds or confidence < 0.70:
            return self.normalizer.failure(
                request,
                "tool.verification_failed",
                f"Visual target could not be verified at confidence >= 0.70 (actual={confidence:.2f})",
            )
        return None

    async def _run_builtin_tool(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
    ) -> ToolResult:
        handlers: dict[
            str, Callable[[ToolRequest, ToolExecutionContext], Awaitable[ToolResult]]
        ] = {
            "mouse.move": self._mouse_move,
            "mouse.click": self._mouse_click,
            "mouse.right_click": self._mouse_click,
            "mouse.double_click": self._mouse_click,
            "mouse.drag": self._mouse_drag,
            "mouse.scroll": self._mouse_scroll,
            "computer.execute": self._computer_execute,
            "keyboard.type_text": self._keyboard_type,
            "keyboard.hotkey": self._keyboard_hotkey,
            "keyboard.press": self._keyboard_hotkey,
            "screen.screenshot": self._screen_screenshot,
            "screen.resolution": self._screen_resolution,
            "screen.analyze": self._screen_analyze,
            "screen.locate_element": self._screen_locate_element,
            "window.active": self._window_active,
            "app.open": self._app_open,
            "app.close": self._app_close,
            "ui.navigate_vision": self._app_open,
            "ui.close_window": self._app_close,
            "shell.run": self._shell_run,
            "filesystem.read": self._filesystem_read,
            "filesystem.write": self._filesystem_write,
            "filesystem.list": self._filesystem_list,
            "filesystem.move": self._filesystem_move,
            "filesystem.delete": self._filesystem_delete,
            "browser.search": self._browser_search,
            "browser.read_page": self._browser_read_page,
            "memory.recall": self._memory_recall,
            "task.wait": self._task_wait,
            "safety.user_idle": self._safety_user_idle,
            "reasoning.raw": self._reasoning_raw,
        }
        handler = handlers.get(definition.id)
        if not handler:
            if definition.id.startswith("mcp.") and self.mcp_manager is not None:
                return await self._run_mcp_tool(definition, request, context)
            if definition.id.startswith("a2a.") and self.a2a_manager is not None:
                return await self._run_a2a_tool(definition, request, context)
            return self.normalizer.failure(
                request, "tool.handler_missing", f"No handler for {definition.id}"
            )
        return await handler(request, context)

    async def _run_mcp_tool(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
    ) -> ToolResult:
        from core.mcp.manager import extract_text

        try:
            result = await asyncio.to_thread(
                self.mcp_manager.call_by_id, definition.id, request.input
            )
        except Exception as exc:  # noqa: BLE001 - surface transport/server errors as failure
            return self.normalizer.failure(request, "mcp.call_failed", str(exc))
        if result.get("isError"):
            return self.normalizer.failure(
                request, "mcp.tool_error", extract_text(result) or "MCP tool returned an error"
            )
        return self.normalizer.success(
            request,
            {"success": True, "text": extract_text(result), "raw": result},
            confidence=0.7,
        )

    async def _run_a2a_tool(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
    ) -> ToolResult:
        try:
            result = await asyncio.to_thread(
                self.a2a_manager.call_by_id, definition.id, request.input
            )
        except Exception as exc:  # noqa: BLE001 - surface transport/agent errors as failure
            return self.normalizer.failure(request, "a2a.call_failed", str(exc))
        if not result.get("success"):
            return self.normalizer.failure(
                request, "a2a.task_failed", result.get("output") or "A2A task did not complete"
            )
        return self.normalizer.success(
            request,
            {"success": True, "text": result.get("output", ""), "raw": result},
            confidence=0.7,
        )

    @staticmethod
    def _feature_flags(context: ToolExecutionContext) -> FeatureFlags:
        explicit = context.extra.get("feature_flags")
        if isinstance(explicit, FeatureFlags):
            return explicit
        if isinstance(explicit, dict):
            return FeatureFlags.from_mapping(explicit, environ={})
        runtime_config = getattr(context.runtime, "config", None)
        flags = getattr(runtime_config, "feature_flags", None)
        if isinstance(flags, FeatureFlags):
            return flags
        return get_config().feature_flags

    def _ui_agent(self, context: ToolExecutionContext) -> Any:
        return context.agent_pool.get("ui_automation")

    def _fs_agent(self, context: ToolExecutionContext) -> Any:
        return context.agent_pool.get("file_system")

    def _search_agent(self, context: ToolExecutionContext) -> Any:
        return context.agent_pool.get("web_search")

    async def _mouse_move(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        x = int(request.input["x"])
        y = int(request.input["y"])
        self.input_monitor.record_agent_mouse_action(x, y)
        ui = self._ui_agent(context)
        sim = getattr(ui, "_sim", None)
        if not sim:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "UI simulator not available"
            )
        await asyncio.to_thread(sim.move_to, x, y)
        return self.normalizer.success(
            request, {"success": True, "final_x": x, "final_y": y}, confidence=0.9
        )

    async def _computer_execute(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
    ) -> ToolResult:
        ui = self._ui_agent(context)
        if not ui:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "UIAutomationAgent not available"
            )
        actions = request.input.get("actions")
        if not isinstance(actions, list) or not actions:
            return self.normalizer.failure(
                request, "computer.actions_required", "Computer execution requires actions"
            )
        normalized_actions: list[dict[str, Any]] = []
        expected = request.metadata.get("expected_outcome")
        for raw in actions:
            if not isinstance(raw, dict):
                return self.normalizer.failure(
                    request, "computer.action_invalid", "Each computer action must be an object"
                )
            action = dict(raw)
            if expected and "expected_outcome" not in action:
                action["expected_outcome"] = expected
            action_type = str(action.get("action") or "").lower()
            if action_type in {
                "click",
                "double_click",
                "right_click",
                "move",
                "scroll",
                "drag",
            }:
                if action_type == "drag":
                    for point in action.get("path") or []:
                        if isinstance(point, (list, tuple)) and len(point) >= 2:
                            self.input_monitor.record_agent_mouse_action(
                                int(point[0]), int(point[1])
                            )
                elif action.get("x") is not None and action.get("y") is not None:
                    self.input_monitor.record_agent_mouse_action(
                        int(action["x"]), int(action["y"])
                    )
            elif action_type in {"type", "key", "hotkey", "press"}:
                self.input_monitor.record_agent_keyboard_action()
            normalized_actions.append(action)
        result = await ui.execute_computer_actions(normalized_actions)
        return self.normalizer.from_action_result(request, result)

    async def _mouse_click(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        ui = self._ui_agent(context)
        if not ui:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "UIAutomationAgent not available"
            )
        x = request.input.get("x")
        y = request.input.get("y")
        if x is not None and y is not None:
            self.input_monitor.record_agent_mouse_action(int(x), int(y))
        click_type = "left"
        if request.tool_id == "mouse.right_click":
            click_type = "right"
        elif request.tool_id == "mouse.double_click":
            click_type = "double"
        else:
            click_type = str(request.input.get("button", "left"))
        step = {
            "target": request.input.get("target") or request.input,
            "click_type": click_type,
            "expected_outcome": request.metadata.get("expected_outcome"),
        }
        action = await ui.click(step)
        return self.normalizer.from_action_result(request, action)

    async def _mouse_drag(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        ui = self._ui_agent(context)
        if not ui:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "UIAutomationAgent not available"
            )
        path = request.input.get("path")
        if not path:
            path = [
                [request.input.get("x1"), request.input.get("y1")],
                [request.input.get("x2"), request.input.get("y2")],
            ]
        if (
            not isinstance(path, list)
            or len(path) < 2
            or any(
                not isinstance(point, (list, tuple))
                or len(point) < 2
                or point[0] is None
                or point[1] is None
                for point in path
            )
        ):
            return self.normalizer.failure(
                request, "mouse.drag_path_invalid", "Drag requires at least two x/y points"
            )
        for point in path:
            self.input_monitor.record_agent_mouse_action(int(point[0]), int(point[1]))
        action = await ui.execute_computer_actions(
            [
                {
                    "action": "drag",
                    "path": [[int(point[0]), int(point[1])] for point in path],
                    "duration_ms": int(request.input.get("duration_ms", 500)),
                    "expected_outcome": request.metadata.get("expected_outcome"),
                }
            ]
        )
        return self.normalizer.from_action_result(request, action)

    async def _mouse_scroll(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        ui = self._ui_agent(context)
        if not ui:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "UIAutomationAgent not available"
            )
        step = {
            "x": request.input.get("x"),
            "y": request.input.get("y"),
            "direction": request.input.get("direction", "down"),
            "amount": request.input.get("amount", 3),
            "expected_outcome": request.metadata.get("expected_outcome"),
        }
        action = await ui.scroll(step)
        return self.normalizer.from_action_result(request, action)

    async def _keyboard_type(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        ui = self._ui_agent(context)
        if not ui:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "UIAutomationAgent not available"
            )
        self.input_monitor.record_agent_keyboard_action()
        action = await ui.type_text(
            {
                "text": request.input.get("text", ""),
                "target": request.input.get("target"),
                "expected_outcome": request.metadata.get("expected_outcome"),
            }
        )
        return self.normalizer.from_action_result(request, action)

    async def _keyboard_hotkey(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        ui = self._ui_agent(context)
        if not ui:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "UIAutomationAgent not available"
            )
        self.input_monitor.record_agent_keyboard_action()
        action = await ui.press_key(
            {
                "keys": request.input.get("keys") or request.input.get("key", ""),
                "expected_outcome": request.metadata.get("expected_outcome"),
            }
        )
        return self.normalizer.from_action_result(request, action)

    async def _screen_screenshot(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        if not context.capture:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "Capture bridge not available"
            )
        frame = await context.capture.capture()
        if not frame:
            return self.normalizer.failure(request, "screen.capture_failed", "No frame returned")
        return self.normalizer.success(request, {"success": True, "frame": frame}, confidence=0.88)

    async def _screen_resolution(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        width, height = context.screen_size
        return self.normalizer.success(
            request, {"success": True, "width": width, "height": height}, confidence=0.98
        )

    async def _screen_analyze(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        frame = await self._screen_screenshot(request, context)
        if not frame.success:
            return frame
        return self.normalizer.success(
            request,
            {
                "success": True,
                "active_window": context.active_window,
                "screen_size": list(context.screen_size),
                "has_frame": True,
            },
            confidence=0.72,
        )

    async def _screen_locate_element(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        text = str(request.input.get("text", ""))
        if not context.capture or not hasattr(context.capture, "find_element"):
            return self.normalizer.failure(
                request, "screen.locator_missing", "Element locator not available"
            )
        element = await context.capture.find_element(text)
        if not element:
            return self.normalizer.failure(
                request, "screen.element_not_found", f"Element not found: {text}"
            )
        bounds = getattr(element, "bounds", None) or element.get("bounds")
        return self.normalizer.success(
            request, {"success": True, "bounds": list(bounds)}, confidence=0.75
        )

    async def _window_active(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        tree = {}
        if context.accessibility and hasattr(context.accessibility, "get_focused_window_tree"):
            tree = await context.accessibility.get_focused_window_tree()
        return self.normalizer.success(
            request,
            {"success": True, "title": context.active_window, "tree": tree or {}},
            confidence=0.86,
        )

    async def _app_open(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        # Pure vision + mouse only. No word-based app names.
        # Expect vision-provided target (description or coords) or use computer actions.
        target = str(request.input.get("target", "") or request.input.get("description", "")).strip()
        if not target:
            return self.normalizer.failure(request, "app.target_missing", "Visual target or description required (no app names)")
        if context.agent_pool.get("ui_automation"):
            ui = context.agent_pool["ui_automation"]
            # Use vision-based navigate (no "app" param)
            action = await ui.navigate({"target": target, "description": target})
            if action.success:
                return self.normalizer.from_action_result(request, action)
            if request.safe_mode:
                return self.normalizer.from_action_result(request, action)
        # Fallback: no shell commands for apps; AI must use mouse/vision.
        return self.normalizer.failure(request, "app.open_vision_only", "App open requires vision + mouse (no name detection or shell)")

    async def _app_close(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        # Pure vision + mouse only. Use computer tool or vision to click close button.
        target = str(request.input.get("target", "") or request.input.get("description", "")).strip()
        if not target:
            return self.normalizer.failure(request, "app.target_missing", "Visual target/description required for close (vision + mouse; no process names)")
        if context.agent_pool.get("ui_automation"):
            ui = context.agent_pool["ui_automation"]
            action = await ui.navigate({"target": target, "description": f"visually locate and close {target} window by clicking X or using Alt+F4"})
            if action.success:
                return self.normalizer.from_action_result(request, action)
        return self.normalizer.failure(request, "app.close_vision_only", "Close requires vision-based mouse action on close button")

    async def _shell_run(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        command = str(request.input.get("command", ""))
        timeout_ms = int(request.input.get("timeout_ms") or 15_000)
        cwd = request.input.get("cwd")
        decision = self.security_policy.validate_shell(command, cwd)
        if not decision.allowed:
            return self.normalizer.failure(
                request,
                decision.code or "shell.blocked",
                decision.reason or "Shell command blocked by policy",
            )

        # Security check via the SecurityAgent in the agent pool
        security_agent = context.agent_pool.get("security")
        if security_agent and hasattr(security_agent, "check_shell_safety"):
            is_safe, reason = security_agent.check_shell_safety(command)
            if not is_safe:
                return self.normalizer.failure(
                    request,
                    "shell.dangerous_command",
                    f"Security agent blocked command: {reason}",
                )

        return await self._run_shell_string(
            request,
            command,
            cwd=Path(str(cwd)),
            timeout_ms=timeout_ms,
        )

    async def _filesystem_read(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        return await self._fs_call(
            request, context, "read_file", {"path": request.input.get("path")}
        )

    async def _filesystem_write(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        return await self._fs_call(
            request,
            context,
            "write_file",
            {"path": request.input.get("path"), "content": request.input.get("content", "")},
        )

    async def _filesystem_list(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        return await self._fs_call(
            request, context, "list_dir", {"path": request.input.get("path", ".")}
        )

    async def _filesystem_move(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        return await self._fs_call(
            request,
            context,
            "move_file",
            {
                "source": request.input.get("source"),
                "destination": request.input.get("destination"),
            },
        )

    async def _filesystem_delete(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        return await self._fs_call(
            request, context, "delete_file", {"path": request.input.get("path")}
        )

    async def _browser_search(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        agent = self._search_agent(context)
        if not agent:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "WebSearchAgent not available"
            )
        action = await agent.search({"query": request.input.get("query", "")})
        return self.normalizer.from_action_result(request, action)

    async def _browser_read_page(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        agent = self._search_agent(context)
        if not agent:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "WebSearchAgent not available"
            )
        action = await agent.read_page({"url": request.input.get("url", "")})
        return self.normalizer.from_action_result(request, action)

    async def _task_wait(self, request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
        duration_ms = max(0, min(int(request.input.get("duration_ms", 500)), 30_000))
        await asyncio.sleep(duration_ms / 1000.0)
        return self.normalizer.success(
            request, {"success": True, "waited_ms": duration_ms}, confidence=1.0
        )

    async def _memory_recall(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        query = str(request.input.get("query", ""))
        memory_agent = context.agent_pool.get("memory")
        if memory_agent and hasattr(memory_agent, "recall"):
            items = await memory_agent.recall(query)
        else:
            items = []
        return self.normalizer.success(request, {"success": True, "items": items}, confidence=0.55)

    async def _safety_user_idle(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        status = self.input_monitor.status
        return self.normalizer.success(
            request,
            {
                "success": True,
                "idle": bool(status.get("mouse_idle")) and bool(status.get("keyboard_idle")),
                "paused": bool(status.get("paused")),
                "reason": status.get("reason"),
            },
            confidence=0.95,
        )

    async def _reasoning_raw(
        self, request: ToolRequest, context: ToolExecutionContext
    ) -> ToolResult:
        return self.normalizer.success(
            request, {"success": True, "raw": request.input}, confidence=0.4
        )

    async def _fs_call(
        self,
        request: ToolRequest,
        context: ToolExecutionContext,
        method_name: str,
        step: dict[str, Any],
    ) -> ToolResult:
        fs = self._fs_agent(context)
        if not fs:
            return self.normalizer.failure(
                request, "tool.dependency_missing", "FileSystemAgent not available"
            )
        action = await getattr(fs, method_name)(step)
        return self.normalizer.from_action_result(request, action)

    async def _run_subprocess_request(
        self,
        request: ToolRequest,
        args: list[str],
        *,
        timeout_ms: int,
    ) -> ToolResult:
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                args,
                capture_output=True,
                text=True,
                timeout=max(1, timeout_ms / 1000.0),
            )
            return (
                self.normalizer.success(
                    request,
                    {
                        "success": proc.returncode == 0,
                        "stdout": proc.stdout.strip(),
                        "stderr": proc.stderr.strip(),
                        "returncode": proc.returncode,
                    },
                    confidence=0.78 if proc.returncode == 0 else 0.35,
                )
                if proc.returncode == 0
                else self.normalizer.failure(
                    request,
                    "process.nonzero_exit",
                    proc.stderr.strip()
                    or proc.stdout.strip()
                    or f"Process exited {proc.returncode}",
                )
            )
        except subprocess.TimeoutExpired:
            return self.normalizer.failure(request, "process.timeout", "Process timed out")
        except Exception as exc:
            return self.normalizer.failure(request, "process.error", str(exc))

    DANGEROUS_COMMANDS = {
        "format",
        "fdisk",
        "dd",
        "mkfs",
        "mke2fs",
        "mkswap",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "init",
        "killall",
        "pkill",
        "kill -9",
        "chmod -r",
        "chown -r",
        "chattr",
        "> /dev/sda",
        "> /dev/hda",
        "> /dev/nvme",
        "| shutdown",
        "| reboot",
        "| poweroff",
        "rm -rf /",
        "rm -rf --no-preserve-root",
        "del /f /s",
        "rd /s /q",
        "format.com",
    }

    async def _run_shell_string(
        self,
        request: ToolRequest,
        command: str,
        *,
        cwd: Path,
        timeout_ms: int,
    ) -> ToolResult:
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(cwd),
                env=self.security_policy.scrub_environment(),
                timeout=max(1, min(timeout_ms / 1000.0, 60.0)),
            )
            output = {
                "success": proc.returncode == 0,
                "stdout": self.security_policy.redact_output(proc.stdout.strip()),
                "stderr": self.security_policy.redact_output(proc.stderr.strip()),
                "returncode": proc.returncode,
                "cwd": str(cwd),
            }
            if proc.returncode != 0:
                return self.normalizer.failure(
                    request,
                    "shell.nonzero_exit",
                    output["stderr"] or output["stdout"] or f"Command exited {proc.returncode}",
                )
            return self.normalizer.success(request, output, confidence=0.78)
        except subprocess.TimeoutExpired:
            return self.normalizer.failure(request, "shell.timeout", "Shell command timed out")
        except Exception as exc:
            return self.normalizer.failure(request, "shell.error", str(exc))

    @staticmethod
    def _is_windows() -> bool:
        import sys

        return sys.platform == "win32"

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
