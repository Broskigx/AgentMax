"""Execution graph primitives for complex tool chains."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TaskPlanner:
    goal: str

    def split(self) -> list[str]:
        return [part.strip() for part in self.goal.split(" and ") if part.strip()] or [self.goal]


@dataclass(slots=True)
class StepPlanner:
    def describe(self, step: dict[str, Any]) -> str:
        return str(step.get("description") or step.get("type") or "tool step")


@dataclass(slots=True)
class ExecutionGraph:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[tuple[int, int]] = field(default_factory=list)

    def add_step(self, step: dict[str, Any], depends_on: int | None = None) -> int:
        idx = len(self.nodes)
        self.nodes.append(step)
        if depends_on is not None:
            self.edges.append((depends_on, idx))
        return idx


@dataclass(slots=True)
class ContextSnapshotManager:
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    def capture(self, *, window: str = "", screen_hash: str = "") -> dict[str, Any]:
        snapshot = {"window": window, "screen_hash": screen_hash}
        self.snapshots.append(snapshot)
        return snapshot


class ScreenStateValidator:
    def validate(self, before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
        if not before or not after:
            return True
        return before.get("window") == after.get("window") or not before.get("window")


class GoalVerifier:
    def verify(self, results: list[Any]) -> bool:
        return bool(results) and any(getattr(result, "success", False) for result in results)


class FailureAnalyzer:
    def analyze(self, error: str | None) -> str:
        if not error:
            return "unknown"
        text = error.lower()
        if "timeout" in text:
            return "timeout"
        if "permission" in text or "denied" in text:
            return "permission"
        if "not found" in text or "missing" in text:
            return "target_missing"
        return "tool_failed"


class RecoveryPlanner:
    def plan(self, failure_reason: str) -> list[str]:
        if failure_reason == "timeout":
            return ["task.wait", "screen.screenshot"]
        if failure_reason == "target_missing":
            return ["screen.screenshot", "screen.locate_element"]
        if failure_reason == "permission":
            return []
        return ["screen.screenshot"]


class MultiStepRunner:
    def should_continue(self, failures: int, max_failures: int = 3) -> bool:
        return failures < max_failures


class ToolChainExecutor:
    def next_tool(self, chain: list[str], completed: set[str]) -> str | None:
        for tool_id in chain:
            if tool_id not in completed:
                return tool_id
        return None
