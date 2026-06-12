"""Typed contracts for the AgentMax tool backend.

The tool layer intentionally sits below planning/reasoning and above concrete
agents.  It gives every operation one stable schema, one permission path, one
risk model, and one normalized result shape.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolExecutionMode(str, Enum):
    INTERNAL = "internal"
    ASYNC = "async"
    SERIALIZED = "serialized"
    SANDBOXED = "sandboxed"


class ToolStatus(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_BY_USER = "paused_by_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _canonical_tool_category(tool_id: str, raw_category: str) -> str:
    if tool_id.startswith("screen."):
        return "screen"
    if tool_id.startswith("keyboard."):
        return "keyboard"
    if tool_id.startswith("mouse."):
        return "mouse"
    if tool_id.startswith("window."):
        return "window"
    if tool_id.startswith("filesystem."):
        return "filesystem"
    if tool_id.startswith("shell."):
        return "shell"
    if tool_id.startswith("browser."):
        return "browser"
    if tool_id.startswith("memory."):
        return "memory"
    if tool_id.startswith("reasoning."):
        return "reasoning"
    if tool_id.startswith("task."):
        return "task"
    if tool_id.startswith("safety."):
        return "safety"
    aliases = {
        "vision": "screen",
        "control": "task",
        "system": "shell",
        "file": "filesystem",
        "network": "browser",
    }
    return aliases.get(raw_category, raw_category)


def _normalize_fallback_chain(raw: list[Any]) -> list[str]:
    aliases = {
        "screenshot": "screen.screenshot",
        "click": "mouse.click",
        "type": "keyboard.type_text",
        "key": "keyboard.hotkey",
        "wait": "task.wait",
    }
    return [aliases.get(str(item), str(item)) for item in raw]


def _normalize_validation_rules(tool_id: str, raw: list[Any]) -> list[str]:
    rules = [str(item) for item in raw]
    if tool_id == "mouse.move":
        for rule in ("coordinates_inside_screen", "user_is_idle", "agent_has_input_control"):
            if rule not in rules:
                rules.append(rule)
    return rules


@dataclass(slots=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_ms: int = 0
    retry_on: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> RetryPolicy:
        raw = raw or {}
        return cls(
            max_attempts=max(1, min(int(raw.get("max_attempts", 1) or 1), 5)),
            backoff_ms=max(0, min(int(raw.get("backoff_ms", 0) or 0), 30_000)),
            retry_on=[str(item) for item in raw.get("retry_on", []) if item],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff_ms": self.backoff_ms,
            "retry_on": self.retry_on,
        }


@dataclass(slots=True)
class ToolDefinition:
    id: str
    name: str
    description: str
    category: str
    version: str = "1.0.0"
    enabled: bool = True
    permissions: list[str] = field(default_factory=list)
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 5_000
    cooldown_ms: int = 0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    fallback_chain: list[str] = field(default_factory=list)
    validation_rules: list[str] = field(default_factory=list)
    requires_user_idle: bool = False
    can_interrupt: bool = True
    execution_mode: ToolExecutionMode = ToolExecutionMode.ASYNC
    safe_mode_support: bool = True
    examples: list[dict[str, Any]] = field(default_factory=list)
    test_cases: list[dict[str, Any]] = field(default_factory=list)
    reasoning_weight: float = 0.5
    internal_tool_priority: int = 50
    execution_cost: float = 1.0
    dependency_map: list[str] = field(default_factory=list)
    confidence_threshold: float = 0.5

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ToolDefinition:
        tool_id = str(raw["id"])
        risk = str(raw.get("risk_level") or raw.get("risk") or "low").lower()
        mode = str(raw.get("execution_mode") or "async").lower()
        return cls(
            id=tool_id,
            name=str(raw.get("name") or raw["id"]),
            description=str(raw.get("description") or ""),
            category=_canonical_tool_category(tool_id, str(raw.get("category") or "uncategorized")),
            version=str(raw.get("version") or "1.0.0"),
            enabled=bool(raw.get("enabled", True)),
            permissions=[str(p) for p in raw.get("permissions", [])],
            risk_level=ToolRiskLevel(risk if risk in ToolRiskLevel._value2member_map_ else "low"),
            input_schema=dict(raw.get("input_schema") or {"type": "object", "properties": {}}),
            output_schema=dict(raw.get("output_schema") or {"type": "object", "properties": {}}),
            timeout_ms=max(1, int(raw.get("timeout_ms") or raw.get("timeout_sec", 5) * 1000)),
            cooldown_ms=max(0, int(raw.get("cooldown_ms", 0) or 0)),
            retry_policy=RetryPolicy.from_dict(raw.get("retry_policy")),
            fallback_chain=_normalize_fallback_chain(list(raw.get("fallback_chain", []))),
            validation_rules=_normalize_validation_rules(
                tool_id, list(raw.get("validation_rules", []))
            ),
            requires_user_idle=bool(raw.get("requires_user_idle", False))
            or tool_id == "mouse.move",
            can_interrupt=bool(raw.get("can_interrupt", True)),
            execution_mode=ToolExecutionMode(
                mode if mode in ToolExecutionMode._value2member_map_ else "async"
            ),
            safe_mode_support=bool(raw.get("safe_mode_support", True)),
            examples=list(raw.get("examples", [])),
            test_cases=list(raw.get("test_cases", [])),
            reasoning_weight=float(raw.get("reasoning_weight", 0.5) or 0.5),
            internal_tool_priority=int(raw.get("internal_tool_priority", 50) or 50),
            execution_cost=float(raw.get("execution_cost", 1.0) or 1.0),
            dependency_map=[str(item) for item in raw.get("dependency_map", [])],
            confidence_threshold=float(raw.get("confidence_threshold", 0.5) or 0.5),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "enabled": self.enabled,
            "permissions": self.permissions,
            "risk_level": self.risk_level.value,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "timeout_ms": self.timeout_ms,
            "cooldown_ms": self.cooldown_ms,
            "retry_policy": self.retry_policy.to_dict(),
            "fallback_chain": self.fallback_chain,
            "validation_rules": self.validation_rules,
            "requires_user_idle": self.requires_user_idle,
            "can_interrupt": self.can_interrupt,
            "execution_mode": self.execution_mode.value,
            "safe_mode_support": self.safe_mode_support,
            "examples": self.examples,
            "test_cases": self.test_cases,
            "reasoning_weight": self.reasoning_weight,
            "internal_tool_priority": self.internal_tool_priority,
            "execution_cost": self.execution_cost,
            "dependency_map": self.dependency_map,
            "confidence_threshold": self.confidence_threshold,
        }


@dataclass(slots=True)
class ToolRequest:
    tool_id: str
    input: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()))
    dry_run: bool = False
    safe_mode: bool = False
    approved_risk: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolExecutionContext:
    task_id: str | None = None
    agent_pool: dict[str, Any] = field(default_factory=dict)
    runtime: Any = None
    capture: Any = None
    accessibility: Any = None
    security: Any = None
    audit: Any = None
    bus: Any = None
    user_idle: bool = True
    screen_size: tuple[int, int] = (1920, 1080)
    active_window: str = ""
    dry_run: bool = False
    safe_mode: bool = False
    approved_risk: bool = False
    cancellation_token: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolThinkingRecord:
    goal: str
    reason_for_tool: str
    selected_tool: str
    risk_level: str
    expected_result: str
    fallback_if_fails: list[str]
    validation_after_execution: str
    requires_user_idle: bool
    confidence: float

    def public_state(self) -> dict[str, Any]:
        return {
            "selected_tool": self.selected_tool,
            "risk_level": self.risk_level,
            "requires_user_idle": self.requires_user_idle,
            "confidence": round(self.confidence, 3),
        }


@dataclass(slots=True)
class ToolResult:
    success: bool
    tool: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    confidence: float = 0.0
    next_recommended_action: str | None = None
    request_id: str | None = None
    task_id: str | None = None
    status: ToolStatus = ToolStatus.COMPLETED
    error_code: str | None = None
    thinking: ToolThinkingRecord | None = None
    attempts: int = 1
    fallback_used: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "tool": self.tool,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 3),
            "confidence": round(self.confidence, 3),
            "next_recommended_action": self.next_recommended_action,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "error_code": self.error_code,
            "attempts": self.attempts,
            "fallback_used": self.fallback_used,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "tool_thinking": self.thinking.public_state() if self.thinking else None,
        }


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    field: str | None = None
    severity: str = "error"


@dataclass(slots=True)
class ValidationReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    @classmethod
    def ok_report(cls) -> ValidationReport:
        return cls(ok=True)

    @classmethod
    def failed(cls, code: str, message: str, field: str | None = None) -> ValidationReport:
        return cls(ok=False, issues=[ValidationIssue(code=code, message=message, field=field)])

    def add(self, code: str, message: str, field: str | None = None) -> None:
        self.ok = False
        self.issues.append(ValidationIssue(code=code, message=message, field=field))

    def error_text(self) -> str:
        return "; ".join(f"{i.code}: {i.message}" for i in self.issues)
