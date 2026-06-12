"""Private reasoning state manager for AgentMax."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.ai.context_analyzer import IntentAnalysis


class ReasoningDepth(str, Enum):
    SHALLOW = "shallow"
    STANDARD = "standard"
    DEEP = "deep"
    CRITICAL = "critical"


class ChecklistStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(slots=True)
class ReasoningChecklistItem:
    """One private verification gate in the internal thinking checklist."""

    id: str
    label: str
    status: ChecklistStatus = ChecklistStatus.PENDING
    details: str = ""
    weight: float = 1.0

    def public_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status.value,
        }


@dataclass(slots=True)
class InternalThoughtState:
    """Private state shared by internal pipeline stages, never returned to users."""

    task_id: str
    state_id: str
    user_input: str
    created_at: float
    mode: str = "INTERNAL_THINKING_MODE"
    intent: str = "general_task"
    reasoning_depth: ReasoningDepth = ReasoningDepth.STANDARD
    confidence_score: float = 0.5
    uncertainty_score: float = 0.5
    risk_score: float = 0.0
    complexity_score: float = 0.0
    ambiguity_score: float = 0.0
    planning_depth: int = 1
    internal_notes: list[str] = field(default_factory=list)
    hidden_execution_plan: list[dict[str, Any]] = field(default_factory=list)
    task_decomposition: list[str] = field(default_factory=list)
    memory_references: list[dict[str, Any]] = field(default_factory=list)
    validation_steps: list[str] = field(default_factory=list)
    objectives: list[str] = field(default_factory=list)
    checklist: list[ReasoningChecklistItem] = field(default_factory=list)
    confidence_factors: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    suggested_tools: list[str] = field(default_factory=list)
    recovery_strategies: list[str] = field(default_factory=list)
    tool_reflections: list[dict[str, Any]] = field(default_factory=list)
    execution_attempts: list[dict[str, Any]] = field(default_factory=list)
    loop_fingerprints: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)

    def public_summary(self) -> dict[str, Any]:
        """Safe diagnostic summary without private notes or hidden planning text."""
        return {
            "state_id": self.state_id,
            "mode": self.mode,
            "intent": self.intent,
            "reasoning_depth": self.reasoning_depth.value,
            "confidence_score": self.confidence_score,
            "uncertainty_score": self.uncertainty_score,
            "risk_score": self.risk_score,
            "complexity_score": self.complexity_score,
            "ambiguity_score": self.ambiguity_score,
            "planning_depth": self.planning_depth,
            "suggested_tools": list(self.suggested_tools),
            "objectives": list(self.objectives[:6]),
            "recovery_strategies": list(self.recovery_strategies[:5]),
            "tool_reflection_count": len(self.tool_reflections),
            "checklist_score": self.checklist_score(),
            "checklist": [item.public_summary() for item in self.checklist],
        }

    def checklist_score(self) -> float:
        if not self.checklist:
            return 0.0
        score = 0.0
        total = 0.0
        for item in self.checklist:
            total += item.weight
            if item.status == ChecklistStatus.PASSED:
                score += item.weight
            elif item.status == ChecklistStatus.WARNING:
                score += item.weight * 0.55
        return round(score / max(total, 1e-6), 3)


class ReasoningManager:
    """Builds and validates private reasoning state."""

    def create_state(
        self, task_id: str, user_input: str, analysis: IntentAnalysis
    ) -> InternalThoughtState:
        depth = self._select_depth(analysis)
        confidence = self._confidence(analysis)
        state = InternalThoughtState(
            task_id=task_id,
            state_id=str(uuid4()),
            user_input=user_input,
            created_at=time.time(),
            intent=analysis.intent,
            reasoning_depth=depth,
            confidence_score=confidence,
            uncertainty_score=self._uncertainty(analysis),
            risk_score=analysis.risk_score,
            complexity_score=analysis.complexity_score,
            ambiguity_score=analysis.ambiguity_score,
            planning_depth=self._planning_depth(depth, analysis),
            memory_references=analysis.memory_references,
            suggested_tools=analysis.suggested_tools,
            task_decomposition=self._decompose(user_input, depth),
            validation_steps=self._validation_steps(analysis),
            objectives=self._objectives(user_input, analysis),
            checklist=self._build_checklist(analysis),
            confidence_factors=self._confidence_factors(analysis),
            risk_factors=self._risk_factors(analysis),
            recovery_strategies=self._recovery_strategies(analysis),
        )
        state.internal_notes.extend(self._internal_notes(analysis))
        return state

    def validate_state(self, state: InternalThoughtState) -> list[str]:
        issues: list[str] = []
        if state.confidence_score < 0.35:
            issues.append("low_confidence")
        if state.uncertainty_score > 0.7:
            issues.append("high_uncertainty")
        if state.ambiguity_score > 0.8 and state.risk_score > 0.5:
            issues.append("ambiguous_high_risk")
        if state.contradictions:
            issues.append("contradictions_detected")
        return issues

    def record_tool_reflection(
        self,
        state: InternalThoughtState,
        *,
        step_type: str,
        success: bool,
        confidence: float,
        error: str | None = None,
    ) -> None:
        reflection = {
            "step_type": step_type,
            "success": success,
            "confidence": round(confidence, 3),
            "error_type": self._error_type(error),
            "ts": round(time.time(), 3),
        }
        state.tool_reflections.append(reflection)
        state.tool_reflections = state.tool_reflections[-30:]
        self.mark_checklist(
            state,
            "tool_reflection",
            ChecklistStatus.PASSED if success else ChecklistStatus.WARNING,
            reflection["error_type"] or "tool result reflected",
        )

    def plan_recovery(
        self,
        state: InternalThoughtState,
        *,
        step_type: str,
        error: str | None,
    ) -> list[str]:
        error_type = self._error_type(error)
        strategies: list[str] = []
        if step_type in {"click", "type", "key", "scroll", "navigate"}:
            strategies.extend(["capture_screen_state", "verify_active_window"])
        if error_type in {"timeout", "network"}:
            strategies.append("retry_with_backoff")
        if error_type in {"missing_target", "no_visual_change"}:
            strategies.extend(["fallback_screenshot", "replan_target"])
        if not strategies:
            strategies.append("fallback_to_safe_diagnostic")
        state.recovery_strategies = list(dict.fromkeys([*state.recovery_strategies, *strategies]))[
            -8:
        ]
        self.mark_checklist(
            state,
            "retry_intelligence",
            ChecklistStatus.PASSED,
            ", ".join(strategies[:4]),
        )
        return strategies

    def mark_checklist(
        self,
        state: InternalThoughtState,
        item_id: str,
        status: ChecklistStatus,
        details: str = "",
    ) -> None:
        for item in state.checklist:
            if item.id == item_id:
                item.status = status
                if details:
                    item.details = details[:240]
                return
        state.checklist.append(
            ReasoningChecklistItem(
                id=item_id,
                label=item_id.replace("_", " "),
                status=status,
                details=details[:240],
            )
        )

    def detect_loop(self, state: InternalThoughtState, marker: str) -> bool:
        fingerprint = hashlib.sha256(marker.encode("utf-8", errors="ignore")).hexdigest()[:16]
        repeated = fingerprint in state.loop_fingerprints[-5:]
        state.loop_fingerprints.append(fingerprint)
        if len(state.loop_fingerprints) > 24:
            state.loop_fingerprints = state.loop_fingerprints[-24:]
        return repeated

    def _select_depth(self, analysis: IntentAnalysis) -> ReasoningDepth:
        if analysis.risk_score >= 0.75:
            return ReasoningDepth.CRITICAL
        if (
            analysis.risk_score >= 0.55
            or analysis.complexity_score >= 0.55
            or analysis.ambiguity_score >= 0.65
        ):
            return ReasoningDepth.DEEP
        if analysis.complexity_score <= 0.2 and analysis.risk_score <= 0.2:
            return ReasoningDepth.SHALLOW
        return ReasoningDepth.STANDARD

    def _confidence(self, analysis: IntentAnalysis) -> float:
        penalty = analysis.ambiguity_score * 0.35 + analysis.risk_score * 0.15
        boost = min(len(analysis.memory_references) * 0.05, 0.15)
        return max(0.05, min(0.98, 0.82 - penalty + boost))

    def _uncertainty(self, analysis: IntentAnalysis) -> float:
        uncertainty = analysis.ambiguity_score * 0.55 + analysis.risk_score * 0.25
        if analysis.missing_context:
            uncertainty += 0.12
        if not analysis.memory_references:
            uncertainty += 0.05
        return round(max(0.02, min(0.98, uncertainty)), 3)

    def _planning_depth(self, depth: ReasoningDepth, analysis: IntentAnalysis) -> int:
        base = {
            ReasoningDepth.SHALLOW: 1,
            ReasoningDepth.STANDARD: 2,
            ReasoningDepth.DEEP: 3,
            ReasoningDepth.CRITICAL: 4,
        }[depth]
        if analysis.missing_context:
            base += 1
        if len(analysis.suggested_tools) >= 2:
            base += 1
        return min(base, 6)

    def _decompose(self, user_input: str, depth: ReasoningDepth) -> list[str]:
        if depth == ReasoningDepth.SHALLOW:
            return ["understand", "act", "validate"]
        if depth == ReasoningDepth.CRITICAL:
            return [
                "understand_intent",
                "identify_destructive_surface",
                "verify_context",
                "gate_dangerous_tools",
                "execute_minimal_plan",
                "validate_postconditions",
                "record_memory",
            ]
        return ["understand_intent", "gather_context", "plan", "execute", "validate", "memorize"]

    def _validation_steps(self, analysis: IntentAnalysis) -> list[str]:
        steps = ["intent_consistency", "tool_schema", "postcondition_check"]
        if analysis.risk_score >= 0.5:
            steps.insert(1, "risk_gate")
        if analysis.memory_references:
            steps.append("memory_crosscheck")
        return steps

    def _objectives(self, user_input: str, analysis: IntentAnalysis) -> list[str]:
        objectives = [
            "understand_user_intent",
            "separate_private_reasoning_from_visible_response",
            "validate_before_acting",
        ]
        if analysis.suggested_tools:
            objectives.append("select_safe_tools")
        if analysis.risk_score >= 0.5:
            objectives.append("gate_high_risk_actions")
        if analysis.memory_references:
            objectives.append("reuse_relevant_memory")
        if len(user_input) > 240 or analysis.complexity_score >= 0.45:
            objectives.append("decompose_task")
        return objectives

    def _build_checklist(self, analysis: IntentAnalysis) -> list[ReasoningChecklistItem]:
        missing_context_status = (
            ChecklistStatus.BLOCKED
            if analysis.requires_clarification
            else ChecklistStatus.WARNING
            if analysis.missing_context
            else ChecklistStatus.PASSED
        )
        risk_status = (
            ChecklistStatus.WARNING if analysis.risk_score >= 0.5 else ChecklistStatus.PASSED
        )
        ambiguity_status = (
            ChecklistStatus.WARNING if analysis.ambiguity_score >= 0.55 else ChecklistStatus.PASSED
        )
        return [
            ReasoningChecklistItem("intent_analysis", "Intent analyzed", ChecklistStatus.PASSED),
            ReasoningChecklistItem("ambiguity_check", "Ambiguity checked", ambiguity_status),
            ReasoningChecklistItem(
                "context_requirements", "Context requirements checked", missing_context_status
            ),
            ReasoningChecklistItem(
                "memory_lookup",
                "Memory references checked",
                ChecklistStatus.PASSED if analysis.memory_references else ChecklistStatus.WARNING,
                weight=0.6,
            ),
            ReasoningChecklistItem(
                "uncertainty_analysis", "Uncertainty analyzed", ChecklistStatus.PASSED
            ),
            ReasoningChecklistItem("risk_assessment", "Risk assessed", risk_status),
            ReasoningChecklistItem(
                "tool_strategy", "Tool strategy selected", ChecklistStatus.PENDING
            ),
            ReasoningChecklistItem(
                "tool_reflection", "Tool reflection armed", ChecklistStatus.PENDING, weight=0.7
            ),
            ReasoningChecklistItem(
                "parameter_validation", "Tool parameters validated", ChecklistStatus.PENDING
            ),
            ReasoningChecklistItem(
                "fallback_strategy", "Fallback strategy prepared", ChecklistStatus.PENDING
            ),
            ReasoningChecklistItem(
                "retry_intelligence", "Retry intelligence prepared", ChecklistStatus.PENDING
            ),
            ReasoningChecklistItem(
                "loop_detection", "Loop detection armed", ChecklistStatus.PASSED, weight=0.7
            ),
            ReasoningChecklistItem(
                "postcondition_validation",
                "Postcondition validation ready",
                ChecklistStatus.PENDING,
            ),
            ReasoningChecklistItem(
                "response_boundary", "Private/visible boundary enforced", ChecklistStatus.PASSED
            ),
            ReasoningChecklistItem(
                "memory_update", "Memory update prepared", ChecklistStatus.PENDING, weight=0.7
            ),
        ]

    def _confidence_factors(self, analysis: IntentAnalysis) -> list[str]:
        factors = []
        if analysis.ambiguity_score < 0.45:
            factors.append("clear_intent")
        if analysis.memory_references:
            factors.append("relevant_memory_found")
        if analysis.suggested_tools:
            factors.append("tool_path_identified")
        return factors

    def _risk_factors(self, analysis: IntentAnalysis) -> list[str]:
        factors = []
        if analysis.risk_score >= 0.5:
            factors.append("potentially_destructive_or_sensitive")
        if analysis.missing_context:
            factors.append("missing_context:" + ",".join(analysis.missing_context))
        if analysis.ambiguity_score >= 0.55:
            factors.append("ambiguous_request")
        return factors

    def _recovery_strategies(self, analysis: IntentAnalysis) -> list[str]:
        strategies = ["validate_postconditions"]
        if analysis.suggested_tools:
            strategies.append("fallback_tool_chain")
        if analysis.ambiguity_score >= 0.55:
            strategies.append("reduce_scope_before_execution")
        if analysis.risk_score >= 0.5:
            strategies.append("require_confirmation_for_destructive_actions")
        if analysis.missing_context:
            strategies.append("gather_missing_context")
        return strategies

    def _error_type(self, error: str | None) -> str:
        text = (error or "").lower()
        if not text:
            return ""
        if "timeout" in text or "timed out" in text:
            return "timeout"
        if "network" in text or "http" in text or "connection" in text:
            return "network"
        if "not found" in text or "missing" in text or "no se encontro" in text:
            return "missing_target"
        if "visual" in text or "no visual" in text:
            return "no_visual_change"
        if "permission" in text or "denied" in text:
            return "permission"
        return "execution_error"

    def _internal_notes(self, analysis: IntentAnalysis) -> list[str]:
        notes = [
            f"intent={analysis.intent}",
            f"tools={','.join(analysis.suggested_tools)}",
        ]
        if analysis.missing_context:
            notes.append(f"missing_context={','.join(analysis.missing_context)}")
        return notes
