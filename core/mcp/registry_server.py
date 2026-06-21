"""Expose AgentMax's tool catalog as an MCP server.

The inverse of :class:`~core.mcp.manager.MCPManager`: instead of pulling remote
MCP tools into AgentMax, this wraps AgentMax's own ``ToolRegistry`` tools as
:class:`~core.mcp.server.MCPTool` handlers so any external MCP client can
discover and call them. Execution is delegated to an injected ``run_tool``
callable (wire it to ``ToolExecutor`` in production; fake it in tests), keeping
this bridge decoupled from the execution pipeline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from core.mcp.server import MCPServer, MCPTool
from core.tools.models import ToolRiskLevel

if TYPE_CHECKING:
    from core.tools.models import ToolDefinition
    from core.tools.registry import ToolRegistry

# (tool_id, arguments) -> result object exposing ``success`` (bool),
# ``output`` (dict) and optionally ``error`` (str).
ToolRunner = Callable[[str, dict[str, Any]], Any]

_TEXT_KEYS = ("text", "stdout", "raw", "items", "result")
_WRITE_SCOPES = {"input", "write", "elevated", "destructive"}


def _output_text(output: dict[str, Any]) -> str:
    for key in _TEXT_KEYS:
        if key in output and output[key]:
            value = output[key]
            return value if isinstance(value, str) else json.dumps(value, default=str)
    return json.dumps(output, default=str) if output else ""


def _annotations(definition: ToolDefinition) -> dict[str, Any]:
    """Derive MCP tool hints from the tool's risk level and permissions."""
    read_only = definition.risk_level == ToolRiskLevel.LOW and not (
        _WRITE_SCOPES & set(definition.permissions)
    )
    destructive = definition.risk_level in {ToolRiskLevel.HIGH, ToolRiskLevel.CRITICAL} or (
        "destructive" in definition.permissions
    )
    return {"readOnlyHint": read_only, "destructiveHint": destructive}


def tool_to_mcp(definition: ToolDefinition, run_tool: ToolRunner) -> MCPTool:
    """Wrap a single :class:`ToolDefinition` as an :class:`MCPTool`."""

    def handler(arguments: dict[str, Any]) -> str:
        result = run_tool(definition.id, arguments)
        if not getattr(result, "success", False):
            error = getattr(result, "error", None)
            raise RuntimeError(error or f"Tool {definition.id} failed")
        return _output_text(getattr(result, "output", {}) or {})

    return MCPTool(
        name=definition.id,
        handler=handler,
        description=definition.description,
        parameters=definition.input_schema or {"type": "object", "properties": {}},
        annotations=_annotations(definition),
    )


def build_tool_mcp_server(
    registry: ToolRegistry,
    run_tool: ToolRunner,
    *,
    include: set[str] | None = None,
) -> MCPServer:
    """Build an :class:`MCPServer` exposing the registry's enabled tools.

    ``include`` optionally restricts the exposed tools to that set of ids.
    """
    tools = [
        tool_to_mcp(definition, run_tool)
        for definition in registry.list(enabled_only=True)
        if include is None or definition.id in include
    ]
    return MCPServer(tools=tools)


__all__ = ["build_tool_mcp_server", "tool_to_mcp", "ToolRunner"]
