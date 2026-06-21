"""Regression tests for tool permission resolution.

Guards against tools whose declared permission scopes cannot be resolved to a
runtime permission (which made them fail the full execution pipeline with
``permission.unknown``), and verifies internal read-only tools run without a
security manager.
"""

from __future__ import annotations

import pytest

from core.tools.executor import ToolExecutor
from core.tools.models import ToolExecutionContext, ToolRequest
from core.tools.permissions import _NO_RUNTIME_PERMISSION, _resolve_permission
from core.tools.registry import ToolRegistry


def test_every_catalog_permission_resolves() -> None:
    registry = ToolRegistry.default()
    unresolved: list[tuple[str, str, str]] = []
    for tool in registry.list():
        if tool.id == "computer.execute":
            continue
        for scope in tool.permissions:
            if (tool.category, scope) in _NO_RUNTIME_PERMISSION:
                continue
            if _resolve_permission(scope, tool.category) is None:
                unresolved.append((tool.id, tool.category, scope))
    assert unresolved == []


@pytest.mark.parametrize("tool_id", ["reasoning.raw", "safety.user_idle"])
async def test_internal_read_tools_run_without_security_manager(tool_id: str) -> None:
    # These are pure in-process tools: they must succeed through the full
    # pipeline even when no security manager is present in the context.
    executor = ToolExecutor(ToolRegistry.default())
    result = await executor.execute(
        ToolRequest(tool_id=tool_id, input={}),
        ToolExecutionContext(),
    )
    assert result.success
    assert result.error_code is None


def test_ui_input_scope_resolves_to_input_mouse() -> None:
    assert _resolve_permission("input", "ui") == "INPUT_MOUSE"
    assert _resolve_permission("read", "ui") == "SCREEN_READ"
