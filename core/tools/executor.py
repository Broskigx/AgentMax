"""Validated execution engine for AgentMax tools."""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

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
    ) -> None:
        self.registry = registry or ToolRegistry.default()
        self.validator = validator or ToolValidator()
        self.permissions = permissions or ToolPermissionManager()
        self.risk = risk or ToolRiskAnalyzer()
        self.queue = queue or ToolQueue()
        self.logger = logger or ToolLogger()
        self.state = state or ToolStateManager()
        self.context_bridge = context_bridge or ToolContextBridge()
        self.fallback = fallback or ToolFallbackManager()
        self.input_monitor = input_monitor or UserInputMonitor()
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
                "computer_control_granted": bool(step.get("_computer_control_granted")),
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
            return result

        self.input_monitor.start_task(request.task_id)
        try:
            result = await self._execute_with_retries(definition, request, context)
            if not result.success:
                result = await self._try_fallbacks(definition, request, context, result)
            return result
        finally:
            self.input_monitor.stop_task()

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
        t0 = time.monotonic()
        can_run, cooldown_reason = self.state.can_run(definition.id)
        if not can_run:
            return self.normalizer.failure(
                request,
                "tool.cooldown",
                cooldown_reason or "Tool cooldown active",
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
                request, "tool.permission_denied", permission.error_text()
            )

        thinking = self._thinking_for(definition, request, risk_report)
        await self.logger.started(definition, request)
        self.state.mark_started(definition.id)

        async def work() -> ToolResult:
            if definition.requires_user_idle and not request.dry_run:
                if self.input_monitor.pause_controller.paused:
                    self.state.mark_paused(definition.id, "paused_by_user")
                    await self.logger.paused(
                        definition.id, request.task_id, "Control pausado por actividad del usuario"
                    )
                    await self.input_monitor.wait_until_safe()
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

    async def _try_fallbacks(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
        failed_result: ToolResult,
    ) -> ToolResult:
        for fallback_request in self.fallback.build_fallback_requests(
            definition, request, failed_result
        ):
            fallback_definition = self.registry.maybe_get(fallback_request.tool_id)
            if not fallback_definition:
                continue
            result = await self._execute_with_retries(
                fallback_definition, fallback_request, context
            )
            result.fallback_used = fallback_request.tool_id
            if result.success:
                return result
        return failed_result

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
            "mouse.scroll": self._mouse_scroll,
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
            return self.normalizer.failure(
                request, "tool.handler_missing", f"No handler for {definition.id}"
            )
        return await handler(request, context)

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
        step = {"target": request.input, "click_type": click_type}
        action = await ui.click(step)
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
        action = await ui.type_text(
            {"text": request.input.get("text", ""), "target": request.input.get("target")}
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
        action = await ui.press_key(
            {"keys": request.input.get("keys") or request.input.get("key", "")}
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
        if not command.strip():
            return self.normalizer.failure(request, "shell.empty", "Shell command is empty")

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

        return await self._run_shell_string(request, command, timeout_ms=timeout_ms)

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
        self, request: ToolRequest, command: str, *, timeout_ms: int
    ) -> ToolResult:
        import structlog

        log = structlog.get_logger(__name__)

        # Security: reject dangerous commands
        cmd_lower = command.lower().strip()
        for dangerous in self.DANGEROUS_COMMANDS:
            if dangerous in cmd_lower:
                log.warning("shell.blocked_dangerous_command", command=command[:120])
                return self.normalizer.failure(
                    request,
                    "shell.blocked",
                    f"Command blocked: contains dangerous pattern '{dangerous}'",
                )

        # Security: cap command length
        if len(command) > 4096:
            return self.normalizer.failure(
                request, "shell.too_long", "Command exceeds 4096 character limit"
            )

        try:
            proc = await asyncio.to_thread(  # noqa: S604 — gated shell sandbox (length-capped + safety-supervised)
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=max(1, min(timeout_ms / 1000.0, 60.0)),
            )
            output = {
                "success": proc.returncode == 0,
                "stdout": proc.stdout.strip()[:50000],
                "stderr": proc.stderr.strip()[:50000],
                "returncode": proc.returncode,
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

    def _start_process(self, target: str) -> int | None:
        # Deprecated for computer control. Use vision + computer tool (mouse) only.
        # Kept for other shell fallbacks, but apps must be opened visually.
        log.warning("executor.start_process_deprecated", target=target, reason="prefer vision+mouse for apps")
        if Path(target).exists():
            proc = subprocess.Popen([target], shell=False)
            return proc.pid
        if self._is_windows():
            proc = subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
            return proc.pid
        proc = subprocess.Popen(shlex.split(target), shell=False)
        return proc.pid

    @staticmethod
    def _is_windows() -> bool:
        import sys

        return sys.platform == "win32"

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
