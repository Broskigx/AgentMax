"""AgentMax Thinking Core: private structured reasoning pipeline."""

from __future__ import annotations

from typing import Any

import structlog

from core.ai.context_analyzer import ContextAnalyzer
from core.ai.planner import ActionToolPlanner, ToolDecision
from core.ai.reasoning_manager import ChecklistStatus, InternalThoughtState, ReasoningManager

log = structlog.get_logger(__name__)

_default_engine: ThinkingEngine | None = None


def get_thinking_engine() -> ThinkingEngine:
    """Return the shared, process-wide ThinkingEngine (created once).

    The engine is a stateless processor — per-call reasoning state lives in the
    objects it returns — so a single shared instance is safe and avoids the
    duplicate construction the IPC layer and Supervisor used to each do.
    """
    global _default_engine
    if _default_engine is None:
        _default_engine = ThinkingEngine()
    return _default_engine


class ThinkingEngine:
    """
    Private reasoning core.

    The engine creates state for INTERNAL_THINKING_MODE, annotates tool plans,
    validates steps before execution and stores compact summaries in memory.
    It never emits chain-of-thought or private notes to the user-facing layer.
    """

    def __init__(self) -> None:
        self._context_analyzer = ContextAnalyzer()
        self._reasoning = ReasoningManager()
        self._tools = ActionToolPlanner()

    async def prepare(
        self,
        *,
        task_id: str,
        user_input: str,
        context: dict[str, Any] | None = None,
    ) -> InternalThoughtState:
        analysis = await self._context_analyzer.analyze(user_input, context)
        state = self._reasoning.create_state(task_id, user_input, analysis)
        state.internal_notes.extend(self._reasoning.validate_state(state))
        if analysis.requires_clarification:
            self._reasoning.mark_checklist(
                state,
                "context_requirements",
                ChecklistStatus.BLOCKED,
                "High-risk ambiguous request requires clarification or confirmation.",
            )
        if state.uncertainty_score >= 0.65:
            self._reasoning.mark_checklist(
                state,
                "uncertainty_analysis",
                ChecklistStatus.WARNING,
                f"uncertainty={state.uncertainty_score:.2f}",
            )

        stm = (context or {}).get("stm")
        if stm is not None:
            stm.put(f"thinking:{task_id}", state.public_summary(), ttl=3600)

        log.info("thinking_core.prepared", task_id=task_id, **state.public_summary())
        return state

    def attach_plan(
        self,
        state: InternalThoughtState,
        steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        enriched = self._tools.enrich_steps(steps)
        state.hidden_execution_plan = enriched
        if enriched:
            self._reasoning.mark_checklist(
                state,
                "tool_strategy",
                ChecklistStatus.PASSED,
                f"{len(enriched)} step(s) annotated with tool policy.",
            )
            has_fallback = any(step.get("fallback_chain") for step in enriched)
            self._reasoning.mark_checklist(
                state,
                "fallback_strategy",
                ChecklistStatus.PASSED if has_fallback else ChecklistStatus.WARNING,
                "Fallback chain present." if has_fallback else "No fallback chain available.",
            )
            has_retry = any(
                (step.get("retry_policy") or {}).get("max_attempts", 1) > 1 for step in enriched
            )
            self._reasoning.mark_checklist(
                state,
                "retry_intelligence",
                ChecklistStatus.PASSED if has_retry else ChecklistStatus.WARNING,
                "Retry policy present." if has_retry else "No retry policy available.",
            )
            self._reasoning.mark_checklist(
                state,
                "postcondition_validation",
                ChecklistStatus.PASSED,
                "Validation pipeline attached to plan steps.",
            )
        else:
            self._reasoning.mark_checklist(
                state,
                "tool_strategy",
                ChecklistStatus.FAILED,
                "Planner returned no executable steps.",
            )
        return enriched

    def decide_step(
        self,
        state: InternalThoughtState | None,
        step: dict[str, Any],
        *,
        confirmed: bool = False,
    ) -> ToolDecision:
        decision = self._tools.decide(step, confirmed=confirmed)
        if state:
            self._reasoning.mark_checklist(
                state,
                "parameter_validation",
                ChecklistStatus.PASSED if decision.allowed else ChecklistStatus.FAILED,
                decision.reason,
            )
            if decision.risk_score >= 0.70:
                self._reasoning.mark_checklist(
                    state,
                    "risk_assessment",
                    ChecklistStatus.PASSED if confirmed else ChecklistStatus.BLOCKED,
                    f"tool_risk={decision.risk_score:.2f}",
                )
            marker = f"{step.get('type')}:{step.get('description')}:{decision.allowed}"
            if self._reasoning.detect_loop(state, marker):
                decision.allowed = False
                decision.reason = "Loop detected for repeated step"
                self._reasoning.mark_checklist(
                    state,
                    "loop_detection",
                    ChecklistStatus.BLOCKED,
                    decision.reason,
                )
        return decision

    def fallback_steps(self, step: dict[str, Any]) -> list[dict[str, Any]]:
        return self._tools.fallback_steps(step)

    def observe_step_result(
        self,
        state: InternalThoughtState | None,
        *,
        step: dict[str, Any],
        success: bool,
        error: str | None = None,
    ) -> None:
        if not state:
            return
        if success:
            state.execution_attempts.append(
                {"step_type": step.get("type", "unknown"), "success": True, "error": None}
            )
            state.execution_attempts = state.execution_attempts[-40:]
            self._reasoning.record_tool_reflection(
                state,
                step_type=str(step.get("type", "unknown")),
                success=True,
                confidence=float(step.get("confidence_threshold", 0.7) or 0.7),
            )
            self._reasoning.mark_checklist(
                state,
                "postcondition_validation",
                ChecklistStatus.PASSED,
                f"{step.get('type', 'unknown')} completed.",
            )
            state.confidence_score = min(0.99, state.confidence_score + 0.02)
            state.uncertainty_score = max(0.02, state.uncertainty_score - 0.02)
            return
        state.execution_attempts.append(
            {
                "step_type": step.get("type", "unknown"),
                "success": False,
                "error": (error or "")[:160],
            }
        )
        state.execution_attempts = state.execution_attempts[-40:]
        self._reasoning.record_tool_reflection(
            state,
            step_type=str(step.get("type", "unknown")),
            success=False,
            confidence=0.0,
            error=error,
        )
        self._reasoning.plan_recovery(
            state,
            step_type=str(step.get("type", "unknown")),
            error=error,
        )
        self._reasoning.mark_checklist(
            state,
            "postcondition_validation",
            ChecklistStatus.WARNING,
            (error or "Step result failed.")[:240],
        )
        state.confidence_score = max(0.05, state.confidence_score - 0.06)
        state.uncertainty_score = min(0.98, state.uncertainty_score + 0.08)

    def recovery_steps(
        self,
        state: InternalThoughtState | None,
        step: dict[str, Any],
        *,
        error: str | None = None,
    ) -> list[dict[str, Any]]:
        if state:
            self._reasoning.plan_recovery(
                state,
                step_type=str(step.get("type", "unknown")),
                error=error,
            )
        fallbacks = self._tools.fallback_steps(step)
        if fallbacks:
            return fallbacks
        step_type = str(step.get("type", "unknown"))
        if step_type in {"click", "type", "key", "scroll", "navigate"}:
            return self._tools.enrich_steps(
                [{"type": "screenshot", "description": "Recovery diagnostic screenshot"}]
            )
        if step_type in {"search", "read_page"}:
            query = step.get("query") or step.get("url") or step.get("description") or ""
            return self._tools.enrich_steps(
                [
                    {
                        "type": "search",
                        "query": str(query)[:240],
                        "description": "Fallback research query",
                    }
                ]
            )
        return []

    def tool_manifest(self) -> list[dict[str, Any]]:
        return self._tools.tool_manifest()

    def mark_memory_updated(self, state: InternalThoughtState | None) -> None:
        if not state:
            return
        self._reasoning.mark_checklist(
            state,
            "memory_update",
            ChecklistStatus.PASSED,
            "Outcome handed to memory layer.",
        )
