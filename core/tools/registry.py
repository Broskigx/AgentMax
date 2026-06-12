"""Tool registry and catalog validation."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.tools.models import ToolDefinition, ValidationReport

REQUIRED_TOOL_FIELDS = {
    "id",
    "name",
    "description",
    "category",
    "version",
    "enabled",
    "permissions",
    "risk_level",
    "input_schema",
    "output_schema",
    "timeout_ms",
    "cooldown_ms",
    "retry_policy",
    "fallback_chain",
    "validation_rules",
    "requires_user_idle",
    "can_interrupt",
    "execution_mode",
    "safe_mode_support",
    "examples",
    "test_cases",
}


class ToolRegistry:
    """Runtime catalog of all tools exposed to planners and executors."""

    def __init__(self, definitions: Iterable[ToolDefinition] | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for definition in definitions or []:
            self.register(definition)

    @classmethod
    def from_file(cls, path: str | Path) -> ToolRegistry:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        items = raw.get("tools", [])
        if isinstance(items, dict):
            normalized = []
            for tool_id, value in items.items():
                if isinstance(value, dict):
                    normalized.append({"id": tool_id, **value})
            items = normalized
        return cls(ToolDefinition.from_dict(item) for item in items)

    @classmethod
    def default(cls) -> ToolRegistry:
        registry = cls.from_file(Path(__file__).resolve().parents[1] / "ai" / "tools.json")
        for raw in _missing_builtin_definitions():
            if not registry.maybe_get(str(raw["id"])):
                registry.register(ToolDefinition.from_dict(raw))
        return registry

    def register(self, definition: ToolDefinition) -> None:
        if not definition.id:
            raise ValueError("Tool id cannot be empty")
        if definition.id in self._tools:
            raise ValueError(f"Duplicate tool id: {definition.id}")
        self._tools[definition.id] = definition

    def get(self, tool_id: str) -> ToolDefinition:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {tool_id}") from exc

    def maybe_get(self, tool_id: str) -> ToolDefinition | None:
        return self._tools.get(tool_id)

    def list(self, *, enabled_only: bool = False) -> list[ToolDefinition]:
        tools = list(self._tools.values())
        if enabled_only:
            tools = [tool for tool in tools if tool.enabled]
        return sorted(tools, key=lambda t: (-t.internal_tool_priority, t.category, t.id))

    def by_category(self) -> dict[str, list[ToolDefinition]]:
        grouped: dict[str, list[ToolDefinition]] = {}
        for tool in self.list():
            grouped.setdefault(tool.category, []).append(tool)
        return grouped

    def validate_catalog(self) -> ValidationReport:
        report = ValidationReport.ok_report()
        seen: set[str] = set()
        for tool in self._tools.values():
            raw = tool.to_dict()
            missing = sorted(REQUIRED_TOOL_FIELDS - set(raw))
            if missing:
                report.add("catalog.missing_fields", f"{tool.id} missing {missing}", tool.id)
            if tool.id in seen:
                report.add("catalog.duplicate_id", f"Duplicate tool id {tool.id}", tool.id)
            seen.add(tool.id)
            if not tool.input_schema.get("type"):
                report.add("catalog.input_schema", f"{tool.id} input_schema missing type", tool.id)
            if not tool.output_schema.get("type"):
                report.add(
                    "catalog.output_schema", f"{tool.id} output_schema missing type", tool.id
                )
            for fallback in tool.fallback_chain:
                if fallback not in self._tools:
                    report.add(
                        "catalog.unknown_fallback",
                        f"{tool.id} fallback references unknown tool {fallback}",
                        tool.id,
                    )
            if tool.timeout_ms <= 0:
                report.add("catalog.timeout", f"{tool.id} timeout_ms must be positive", tool.id)
            if tool.retry_policy.max_attempts > 5:
                report.add("catalog.retry", f"{tool.id} max_attempts too high", tool.id)
        return report

    def snapshot(self) -> dict[str, Any]:
        categories = {
            category: [tool.to_dict() for tool in tools]
            for category, tools in self.by_category().items()
        }
        validation = self.validate_catalog()
        return {
            "count": len(self._tools),
            "categories": categories,
            "valid": validation.ok,
            "issues": [asdict(issue) for issue in validation.issues],
        }


def _missing_builtin_definitions() -> list[dict[str, Any]]:
    base_schema = {"type": "object", "properties": {}}
    return [
        {
            "id": "screen.resolution",
            "name": "Screen Resolution",
            "description": "Read current screen resolution.",
            "category": "screen",
            "risk_level": "low",
            "permissions": ["read"],
            "input_schema": base_schema,
            "output_schema": {
                "type": "object",
                "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}},
            },
            "timeout_ms": 3000,
            "fallback_chain": [],
            "validation_rules": [],
            "requires_user_idle": False,
            "can_interrupt": False,
            "execution_mode": "internal",
            "safe_mode_support": True,
            "examples": [],
            "test_cases": [{"name": "dry_run", "input": {}, "expect_success": True}],
        },
        {
            "id": "screen.analyze",
            "name": "Analyze Screen",
            "description": "Analyze current screen metadata after capture.",
            "category": "screen",
            "risk_level": "low",
            "permissions": ["read"],
            "input_schema": base_schema,
            "output_schema": {"type": "object", "properties": {"has_frame": {"type": "boolean"}}},
            "timeout_ms": 6000,
            "fallback_chain": ["screen.screenshot"],
            "validation_rules": [],
            "requires_user_idle": False,
            "can_interrupt": True,
            "execution_mode": "async",
            "safe_mode_support": True,
            "examples": [],
            "test_cases": [{"name": "dry_run", "input": {}, "expect_success": True}],
        },
        {
            "id": "screen.locate_element",
            "name": "Locate Element",
            "description": "Locate a visible element by text when OCR/accessibility is available.",
            "category": "screen",
            "risk_level": "low",
            "permissions": ["read"],
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"bounds": {"type": "array"}}},
            "timeout_ms": 8000,
            "fallback_chain": ["screen.screenshot"],
            "validation_rules": [],
            "requires_user_idle": False,
            "can_interrupt": True,
            "execution_mode": "async",
            "safe_mode_support": True,
            "examples": [],
            "test_cases": [{"name": "dry_run", "input": {"text": "OK"}, "expect_success": True}],
        },
        {
            "id": "window.active",
            "name": "Active Window",
            "description": "Read the focused window title/tree.",
            "category": "window",
            "risk_level": "low",
            "permissions": ["read"],
            "input_schema": base_schema,
            "output_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
            "timeout_ms": 5000,
            "fallback_chain": [],
            "validation_rules": [],
            "requires_user_idle": False,
            "can_interrupt": True,
            "execution_mode": "async",
            "safe_mode_support": True,
            "examples": [],
            "test_cases": [{"name": "dry_run", "input": {}, "expect_success": True}],
        },
        {
            "id": "memory.recall",
            "name": "Recall Memory",
            "description": "Read relevant local memory snippets.",
            "category": "memory",
            "risk_level": "low",
            "permissions": ["read"],
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"items": {"type": "array"}}},
            "timeout_ms": 5000,
            "fallback_chain": [],
            "validation_rules": [],
            "requires_user_idle": False,
            "can_interrupt": True,
            "execution_mode": "async",
            "safe_mode_support": True,
            "examples": [],
            "test_cases": [{"name": "dry_run", "input": {"query": "task"}, "expect_success": True}],
        },
        {
            "id": "safety.user_idle",
            "name": "User Idle Check",
            "description": "Check whether user is currently controlling mouse/keyboard.",
            "category": "safety",
            "risk_level": "low",
            "permissions": ["read"],
            "input_schema": base_schema,
            "output_schema": {"type": "object", "properties": {"idle": {"type": "boolean"}}},
            "timeout_ms": 3000,
            "fallback_chain": [],
            "validation_rules": [],
            "requires_user_idle": False,
            "can_interrupt": False,
            "execution_mode": "internal",
            "safe_mode_support": True,
            "examples": [],
            "test_cases": [{"name": "dry_run", "input": {}, "expect_success": True}],
        },
        {
            "id": "reasoning.raw",
            "name": "Reasoning Payload",
            "description": "Pass a sanitized reasoning payload through tool normalization.",
            "category": "reasoning",
            "risk_level": "low",
            "permissions": ["read"],
            "input_schema": base_schema,
            "output_schema": {"type": "object", "properties": {"raw": {"type": "object"}}},
            "timeout_ms": 3000,
            "fallback_chain": [],
            "validation_rules": [],
            "requires_user_idle": False,
            "can_interrupt": False,
            "execution_mode": "internal",
            "safe_mode_support": True,
            "examples": [],
            "test_cases": [{"name": "dry_run", "input": {}, "expect_success": True}],
        },
    ]
