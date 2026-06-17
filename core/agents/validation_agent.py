"""Validation Agent -- verifies action outcomes and triggers auto-correction."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

from core.agents.base_agent import ActionResult, AgentCapability, AgentContext, BaseAgent

if TYPE_CHECKING:
    from core.agents.supervisor import TaskRecord

log = structlog.get_logger(__name__)


class ValidationAgent(BaseAgent):
    """
    After each task and step, validates that the expected state was achieved.

    Strategies:
      1. Text presence -- expected text visible on screen
      2. Element state -- element is in expected state (enabled, checked, etc.)
      3. Visual diff -- screen changed as expected
      4. Window state -- correct window is active
      5. Claude semantic -- ask Claude to verify outcome
    """

    VERIFY_DELAY_SEC = 0.3

    def __init__(self, ctx: AgentContext) -> None:
        super().__init__(ctx)
        self._validation_history: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "validation"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [
            AgentCapability("validate_task", "Verify full task completion", requires_screen=True),
            AgentCapability("validate_step", "Verify single step outcome", requires_screen=True),
        ]

    async def validate_task(self, record: TaskRecord) -> bool:
        if not record.plan:
            return True

        await asyncio.sleep(self.VERIFY_DELAY_SEC)

        failed_steps = [r for r in record.results if not r.success]
        critical_failures = [
            (i, r)
            for i, (step, r) in enumerate(zip(record.plan.steps, record.results, strict=False))
            if not r.success and step.get("critical", False)
        ]

        if critical_failures:
            log.warning(
                "validation.critical_failures",
                count=len(critical_failures),
                task_id=record.request.id,
            )
            await self._attempt_recovery(record, critical_failures)
            return False

        success_rate = (len(record.results) - len(failed_steps)) / max(len(record.results), 1)
        log.info(
            "validation.task_result",
            task_id=record.request.id,
            success_rate=f"{success_rate:.0%}",
            failed=len(failed_steps),
        )

        self._validation_history.append(
            {
                "task_id": record.request.id,
                "success_rate": success_rate,
                "timestamp": time.time(),
            }
        )

        return success_rate >= 0.8

    async def validate_step(self, step: dict, result: ActionResult, vision_agent: Any) -> bool:
        expected = step.get("expected_outcome")
        if not expected or not vision_agent:
            return result.success

        await asyncio.sleep(self.VERIFY_DELAY_SEC)
        text_found = await vision_agent.verify_text_on_screen(str(expected))

        if not text_found and step.get("expected_window"):
            active = await self.ctx.accessibility.get_active_window_title()
            text_found = step["expected_window"].lower() in (active or "").lower()

        log.debug("validation.step", expected=expected, found=text_found)
        return text_found

    async def detect_error_dialogs(self) -> list[dict[str, str]]:
        """Check for common Windows error dialogs."""
        vision = self.ctx.runtime._agent_pool.get("vision")
        if not vision:
            return []

        error_patterns = [
            "error",
            "warning",
            "failed",
            "cannot",
            "unable to",
            "access denied",
            "not found",
            "exception",
            "crashed",
        ]

        state = await vision.get_current_state()
        if not state:
            return []

        dialogs = []
        for el in state.elements:
            for pattern in error_patterns:
                if pattern in el.text.lower():
                    dialogs.append({"text": el.text, "bounds": list(el.bounds)})
                    break

        return dialogs

    async def _attempt_recovery(self, record: TaskRecord, failures: list) -> None:
        log.info("validation.attempting_recovery", task_id=record.request.id)

        error_dialogs = await self.detect_error_dialogs()
        if error_dialogs:
            ui_agent = self.ctx.runtime._agent_pool.get("ui_automation")
            if ui_agent:
                for _dialog in error_dialogs:
                    await ui_agent.press_key({"keys": "escape"})
                    await asyncio.sleep(0.2)
                    await ui_agent.press_key({"keys": "enter"})
                    await asyncio.sleep(0.2)
