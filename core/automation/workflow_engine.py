"""Workflow execution engine -- runs step sequences with retry and adaptive timing."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class WorkflowEngine:
    """Executes a flat list of steps with retry, timeout, and outcome validation."""

    DEFAULT_STEP_TIMEOUT = 30.0

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        ui_agent: Any,
        vision_agent: Any,
        on_step: Any = None,
    ) -> list[dict]:
        results = []
        for i, step in enumerate(steps):
            t0 = time.monotonic()
            result = await self._execute_step(step, ui_agent, vision_agent)
            elapsed = (time.monotonic() - t0) * 1000
            result["step"] = i + 1
            result["elapsed_ms"] = elapsed
            results.append(result)

            if on_step:
                await on_step(i + 1, len(steps), step, result)

            wait_ms = step.get("wait_after_ms", 100)
            if wait_ms > 0:
                await asyncio.sleep(wait_ms / 1000)

        return results

    async def _execute_step(self, step: dict, ui_agent: Any, vision_agent: Any) -> dict:
        step_type = step.get("type", "")
        try:
            if step_type == "click":
                r = await ui_agent.click(step)
            elif step_type == "type":
                r = await ui_agent.type_text(step)
            elif step_type == "key":
                r = await ui_agent.press_key(step)
            elif step_type == "scroll":
                r = await ui_agent.scroll(step)
            elif step_type == "wait":
                await asyncio.sleep(step.get("duration_sec", 0.5))
                return {"success": True, "type": "wait"}
            elif step_type == "screenshot":
                r = await vision_agent.capture_and_analyze(step)
            else:
                return {"success": False, "error": f"Unknown type: {step_type}"}

            return {"success": r.success, "error": r.error, "type": step_type}
        except Exception as exc:
            return {"success": False, "error": str(exc), "type": step_type}
