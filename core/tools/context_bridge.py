"""Build tool execution context from the existing AgentMax runtime."""

from __future__ import annotations

from typing import Any

from core.tools.models import ToolExecutionContext, ToolRequest


class ToolContextBridge:
    async def build(
        self,
        *,
        request: ToolRequest,
        runtime: Any = None,
        agent_pool: dict[str, Any] | None = None,
        capture: Any = None,
        accessibility: Any = None,
        security: Any = None,
        audit: Any = None,
        bus: Any = None,
        user_idle: bool = True,
        extra: dict[str, Any] | None = None,
    ) -> ToolExecutionContext:
        runtime = runtime or (agent_pool or {}).get("runtime")
        if runtime:
            agent_pool = agent_pool or getattr(runtime, "_agent_pool", {})
            capture = capture or getattr(runtime, "capture", None)
            accessibility = accessibility or getattr(runtime, "accessibility", None)
            security = security or getattr(runtime, "security", None)
            audit = audit or getattr(runtime, "audit", None)
            bus = bus or getattr(runtime, "bus", None)

        screen_size = (1920, 1080)
        if capture and hasattr(capture, "get_screen_dimensions"):
            try:
                screen_size = await capture.get_screen_dimensions()
            except Exception:
                pass

        active_window = ""
        if accessibility and hasattr(accessibility, "get_active_window_title"):
            try:
                active_window = await accessibility.get_active_window_title()
            except Exception:
                pass

        return ToolExecutionContext(
            task_id=request.task_id,
            agent_pool=agent_pool or {},
            runtime=runtime,
            capture=capture,
            accessibility=accessibility,
            security=security,
            audit=audit,
            bus=bus,
            user_idle=user_idle,
            screen_size=screen_size,
            active_window=active_window,
            dry_run=request.dry_run,
            safe_mode=request.safe_mode,
            approved_risk=request.approved_risk,
            extra=extra or {},
        )
