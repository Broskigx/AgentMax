"""Dry-run and diagnostic runner for the AgentMax tool system."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from core.tools.context_bridge import ToolContextBridge
from core.tools.executor import ToolExecutor
from core.tools.logger import ToolLogger
from core.tools.models import ToolRequest
from core.tools.registry import ToolRegistry


@dataclass(slots=True)
class ToolTestReport:
    ok: bool
    total: int
    passed: int
    failed: int
    duration_ms: float
    results: list[dict[str, Any]] = field(default_factory=list)


class ToolTestingRunner:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry.default()
        self.executor = executor or ToolExecutor(
            self.registry,
            logger=ToolLogger(emit_structlog=False),
        )
        self.context_bridge = ToolContextBridge()

    async def validate_json(self) -> ToolTestReport:
        t0 = time.monotonic()
        validation = self.registry.validate_catalog()
        return ToolTestReport(
            ok=validation.ok,
            total=1,
            passed=1 if validation.ok else 0,
            failed=0 if validation.ok else 1,
            duration_ms=(time.monotonic() - t0) * 1000,
            results=[
                {
                    "name": "tools.json",
                    "success": validation.ok,
                    "issues": [asdict(issue) for issue in validation.issues],
                }
            ],
        )

    async def doctor(self) -> ToolTestReport:
        t0 = time.monotonic()
        validation = self.registry.validate_catalog()
        categories = sorted(self.registry.by_category())
        checks = [
            {"name": "catalog_valid", "success": validation.ok},
            {"name": "has_mouse_tools", "success": "mouse" in categories},
            {"name": "has_keyboard_tools", "success": "keyboard" in categories},
            {"name": "has_safety_tools", "success": "safety" in categories},
            {"name": "has_task_tools", "success": "task" in categories},
        ]
        failed = len([item for item in checks if not item["success"]])
        return ToolTestReport(
            ok=failed == 0,
            total=len(checks),
            passed=len(checks) - failed,
            failed=failed,
            duration_ms=(time.monotonic() - t0) * 1000,
            results=checks,
        )

    async def run(
        self,
        *,
        category: str | None = None,
        safe: bool = True,
        integration: bool = False,
    ) -> ToolTestReport:
        t0 = time.monotonic()
        context = await self.context_bridge.build(
            request=ToolRequest(tool_id="task.wait", dry_run=True),
            user_idle=True,
            extra={"input_control": True},
        )
        results: list[dict[str, Any]] = []
        tools = self.registry.list(enabled_only=True)
        if category:
            tools = [tool for tool in tools if tool.category == category]

        for tool in tools:
            if safe and not tool.safe_mode_support:
                continue
            cases = tool.test_cases or [
                {"name": "dry_run", "input": self._sample_input(tool), "expect_success": True}
            ]
            for case in cases:
                request = ToolRequest(
                    tool_id=tool.id,
                    input=dict(case.get("input") or {}),
                    dry_run=not integration,
                    safe_mode=safe,
                    approved_risk=bool(case.get("approved_risk", False)),
                )
                result = await self.executor.execute(request, context)
                expected = bool(case.get("expect_success", True))
                passed = result.success is expected
                results.append(
                    {
                        "name": f"{tool.id}:{case.get('name', 'case')}",
                        "success": passed,
                        "tool_success": result.success,
                        "error": result.error,
                        "dry_run": request.dry_run,
                    }
                )

        failed = len([item for item in results if not item["success"]])
        return ToolTestReport(
            ok=failed == 0,
            total=len(results),
            passed=len(results) - failed,
            failed=failed,
            duration_ms=(time.monotonic() - t0) * 1000,
            results=results,
        )

    @staticmethod
    def to_dict(report: ToolTestReport) -> dict[str, Any]:
        return {
            "ok": report.ok,
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "duration_ms": round(report.duration_ms, 3),
            "results": report.results,
        }

    @staticmethod
    def _sample_input(tool: Any) -> dict[str, Any]:
        if tool.examples:
            first = tool.examples[0]
            if isinstance(first, dict) and isinstance(first.get("input"), dict):
                return dict(first["input"])

        props = tool.input_schema.get("properties", {})
        sample: dict[str, Any] = {}
        for field_name in tool.input_schema.get("required", []):
            schema = props.get(field_name, {})
            expected = schema.get("type")
            if expected == "integer":
                sample[field_name] = max(0, int(schema.get("minimum", 1) or 1))
            elif expected == "number":
                sample[field_name] = max(0.1, float(schema.get("minimum", 1.0) or 1.0))
            elif expected == "array":
                sample[field_name] = []
            elif expected == "object":
                sample[field_name] = {}
            else:
                sample[field_name] = "test"
        return sample
