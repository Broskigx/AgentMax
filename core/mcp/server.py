"""MCP Server — exposes a set of callables as MCP-discoverable tools.

Unlike the OpenJarvis original (which auto-discovers framework-specific tool
classes), this is a generic, handler-based server: register any callable with
an ``MCPTool`` and it becomes available over the MCP protocol. This keeps the
server decoupled from AgentMax's ``ToolExecutor`` pipeline while still allowing
AgentMax tools to be wrapped and exposed when desired.

Adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis),
licensed under the Apache License 2.0. See core/mcp/NOTICE.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    MCPRequest,
    MCPResponse,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MCPTool:
    """A single tool exposed by :class:`MCPServer`.

    ``handler`` receives the call ``arguments`` dict and returns either a
    string (used directly as text content) or any JSON-serializable value
    (stringified for the text content block).
    """

    name: str
    handler: Callable[[dict[str, Any]], Any]
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    annotations: dict[str, Any] | None = None


class MCPServer:
    """MCP server that exposes registered :class:`MCPTool` callables via JSON-RPC."""

    SERVER_NAME = "agentmax"
    SERVER_VERSION = "0.1.0"
    PROTOCOL_VERSION = "2025-11-25"

    def __init__(self, tools: list[MCPTool] | None = None) -> None:
        self._tools: dict[str, MCPTool] = {t.name: t for t in (tools or [])}

    def add_tool(self, tool: MCPTool) -> None:
        """Register (or replace) a tool by name."""
        self._tools[tool.name] = tool

    def handle(self, request: MCPRequest) -> MCPResponse:
        """Dispatch an MCP request and return a response."""
        if request.method == "initialize":
            return self._handle_initialize(request)
        if request.method == "tools/list":
            return self._handle_tools_list(request)
        if request.method == "tools/call":
            return self._handle_tools_call(request)
        if request.method.startswith("notifications/"):
            # Notifications expect no response body; return an empty ack.
            return MCPResponse(result={}, id=request.id if request.id is not None else 0)
        return MCPResponse.error_response(
            request.id if request.id is not None else 0,
            METHOD_NOT_FOUND,
            f"Unknown method: {request.method}",
        )

    def _handle_initialize(self, req: MCPRequest) -> MCPResponse:
        """Handle the initialize handshake."""
        return MCPResponse(
            result={
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {
                    "name": self.SERVER_NAME,
                    "version": self.SERVER_VERSION,
                    "title": "AgentMax Tool Server",
                },
            },
            id=req.id if req.id is not None else 0,
        )

    def _handle_tools_list(self, req: MCPRequest) -> MCPResponse:
        """Handle tools/list — return specs for all registered tools."""
        tool_list: list[dict[str, Any]] = []
        for tool in self._tools.values():
            entry: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,
            }
            if tool.annotations:
                entry["annotations"] = tool.annotations
            tool_list.append(entry)
        return MCPResponse(result={"tools": tool_list}, id=req.id if req.id is not None else 0)

    def _handle_tools_call(self, req: MCPRequest) -> MCPResponse:
        """Handle tools/call — execute a tool and return the result."""
        req_id = req.id if req.id is not None else 0
        tool_name = req.params.get("name")
        arguments = req.params.get("arguments", {})

        if not tool_name:
            return MCPResponse.error_response(
                req_id, INVALID_PARAMS, "Missing required parameter: name"
            )
        tool = self._tools.get(tool_name)
        if tool is None:
            return MCPResponse.error_response(req_id, INVALID_PARAMS, f"Unknown tool: {tool_name}")

        try:
            raw = tool.handler(arguments if isinstance(arguments, dict) else {})
            text = raw if isinstance(raw, str) else str(raw)
            return MCPResponse(
                result={"content": [{"type": "text", "text": text}], "isError": False},
                id=req_id,
            )
        except Exception as exc:  # noqa: BLE001 - report any handler error over the wire
            logger.warning("MCP tool %r failed: %s", tool_name, exc)
            return MCPResponse.error_response(
                req_id, INTERNAL_ERROR, f"Tool execution error: {exc}"
            )


__all__ = ["MCPServer", "MCPTool"]
