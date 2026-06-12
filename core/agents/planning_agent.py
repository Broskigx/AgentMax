"""Planning Agent -- uses Claude to decompose tasks into executable step sequences."""

from __future__ import annotations

import json
from typing import Any

import structlog

from core.agents.base_agent import AgentCapability, AgentContext, BaseAgent
from core.agents.supervisor import TaskPlan, TaskRequest
from core.ai.ai_router import AIRouter
from core.ai.prompt_templates import PLANNING_SYSTEM_PROMPT, build_planning_prompt

log = structlog.get_logger(__name__)


def _build_capability_fallback_plan(task_id: str, description: str, reason: str = "") -> TaskPlan:
    """
    Emergency fallback used ONLY when the AI model is completely unavailable.
    Does NOT use keyword matching, app aliases, or pre-programmed actions.
    Just takes a screenshot so the system can report the failure gracefully
    and let the agent retry or escalate.
    """
    steps: list[dict[str, Any]] = [
        {
            "type": "screenshot",
            "description": "Model unavailable — capturing screen state for diagnostics.",
            "critical": False,
            "expected_outcome": "Screen state captured for error reporting.",
        }
    ]

    if reason:
        steps[0]["planning_fallback_reason"] = reason[:240]

    return TaskPlan(
        task_id=task_id,
        steps=steps,
        estimated_duration_sec=3.0,
        risk_level="medium",
        requires_confirmation=False,
    )


class PlanningAgent(BaseAgent):
    """
    Receives a natural-language task description and produces a structured
    execution plan by querying Claude with a screen context snapshot.

    Plan format (JSON):
    {
      "steps": [
        {
          "type": "click|type|key|scroll|wait|screenshot|navigate",
          "description": "human-readable",
          "target": {"text": "...", "region": [x,y,w,h]},
          "value": "...",
          "critical": true,
          "expected_outcome": "..."
        }
      ],
      "risk_level": "low|medium|high|critical",
      "estimated_duration_sec": 5.0,
      "requires_confirmation": false,
      "reasoning": "..."
    }
    """

    def __init__(self, ctx: AgentContext) -> None:
        super().__init__(ctx)
        self._claude = AIRouter(ctx.config)

    @property
    def name(self) -> str:
        return "planning"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [
            AgentCapability("plan", "Decomposes tasks into executable steps using Claude vision")
        ]

    async def plan(self, request: TaskRequest) -> TaskPlan:
        log.info("planning.start", task_id=request.id, description=request.description)

        # Always capture the screen and accessibility tree before planning.
        # The AI model reasons about what it sees — no keyword shortcuts.
        screenshot_b64 = await self.ctx.capture.capture_base64()
        accessibility_tree = await self.ctx.accessibility.get_focused_window_tree()

        prompt = build_planning_prompt(
            task=request.description,
            accessibility_summary=json.dumps(accessibility_tree, indent=2)[:4000],
        )

        raw: str = ""
        try:
            # Use vision only when we have a screenshot.
            # When the Rust server is unavailable or the model is text-only,
            # the accessibility tree (embedded in `prompt`) is the primary
            # context source -- avoids empty-image 400 errors on text models.
            if screenshot_b64:
                raw = await self._claude.vision_query(
                    system=PLANNING_SYSTEM_PROMPT,
                    prompt=prompt,
                    image_b64=screenshot_b64,
                )
            else:
                log.info("planning.text_only_mode", reason="no_screenshot")
                raw = await self._claude.text_query(
                    system=PLANNING_SYSTEM_PROMPT,
                    prompt=prompt,
                )
        except Exception as exc:
            # Common case: the local backend rejects vision payloads because
            # the loaded GGUF doesn't ship the vision encoder. Detect that
            # specific signature and retry as text-only with the accessibility
            # tree -- enough for "open X" style tasks. Reversible: drop this
            # inner try to restore the prior "single attempt then fallback"
            # behaviour.
            err_str = str(exc).lower()
            looks_like_vision_reject = bool(screenshot_b64) and any(
                marker in err_str
                for marker in ("400", "image", "audio", "multimodal", "unsupported")
            )
            if looks_like_vision_reject:
                log.warning(
                    "planning.vision_rejected_retrying_text_only",
                    error=str(exc)[:200],
                )
                try:
                    raw = await self._claude.text_query(
                        system=PLANNING_SYSTEM_PROMPT,
                        prompt=prompt,
                    )
                except Exception as exc2:
                    log.warning("planning.model_unavailable_fallback", error=str(exc2))
                    return _build_capability_fallback_plan(
                        request.id, request.description, str(exc2)
                    )
            else:
                log.warning("planning.model_unavailable_fallback", error=str(exc))
                return _build_capability_fallback_plan(request.id, request.description, str(exc))

        plan_data = self._parse_plan(raw, request.id)
        # If the model returned an unparseable or trivially empty plan, fall back
        # to a screenshot-only plan so the agent can observe and replan.
        if len(plan_data.steps) == 1 and plan_data.steps[0].get("type") == "raw":
            plan_data = _build_capability_fallback_plan(
                request.id,
                request.description,
                "model_plan_parse_failed",
            )
        log.info(
            "planning.complete",
            task_id=request.id,
            steps=len(plan_data.steps),
            risk=plan_data.risk_level,
        )
        return plan_data

    def _normalize_model_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pass steps through unchanged — the model already produced typed desktop actions."""
        return steps

    def _parse_plan(self, raw: str, task_id: str) -> TaskPlan:
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
            else:
                raise ValueError("No JSON found in response")
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("planning.parse_fallback", error=str(exc))
            data = {
                "steps": [{"type": "raw", "description": raw[:500], "critical": False}],
                "risk_level": "medium",
                "estimated_duration_sec": 10.0,
                "requires_confirmation": True,
                "reasoning": "Fallback plan due to parse error",
            }

        return TaskPlan(
            task_id=task_id,
            steps=data.get("steps", []),
            estimated_duration_sec=float(data.get("estimated_duration_sec", 10.0)),
            risk_level=data.get("risk_level", "medium"),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
        )

    async def replan(self, task_id: str, reason: str, previous_steps: list[dict]) -> list[dict]:
        """Re-plan from current screen state when a step fails."""
        screenshot_b64 = await self.ctx.capture.capture_base64()
        prompt = (
            f"Previous steps attempted: {json.dumps(previous_steps)}\n"
            f"Failure reason: {reason}\n"
            "Generate corrected remaining steps as JSON array."
        )
        raw = await self._claude.vision_query(
            system=PLANNING_SYSTEM_PROMPT,
            prompt=prompt,
            image_b64=screenshot_b64,
        )
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            return json.loads(raw[start:end])
        except Exception:
            return []
