"""MCP Client — connects to MCP servers and discovers/calls tools.

Adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis),
licensed under the Apache License 2.0. See core/mcp/NOTICE.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from core.mcp.protocol import MCPError, MCPRequest, MCPResponse
from core.mcp.transport import MCPTransport


@dataclass(slots=True)
class MCPToolSpec:
    """Lightweight description of a tool advertised by an MCP server.

    Kept intentionally decoupled from AgentMax's richer ``ToolDefinition``.
    The :class:`~core.mcp.manager.MCPManager` is responsible for mapping these
    specs into ``ToolDefinition`` instances for the tool registry.
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 600.0


class MCPClient:
    """Client that communicates with an MCP server via a transport.

    Parameters
    ----------
    transport:
        The transport layer to use for communication.
    """

    def __init__(self, transport: MCPTransport) -> None:
        self._transport = transport
        self._initialized = False
        self._capabilities: dict[str, Any] = {}
        self._id_counter = itertools.count(1)

    def _next_id(self) -> int:
        return next(self._id_counter)

    def _send(self, method: str, params: dict[str, Any] | None = None) -> MCPResponse:
        """Send a request and check for errors."""
        request = MCPRequest(
            method=method,
            params=params or {},
            id=self._next_id(),
        )
        response = self._transport.send(request)
        if response.error is not None:
            raise MCPError(
                code=response.error.get("code", -1),
                message=response.error.get("message", "Unknown error"),
                data=response.error.get("data"),
            )
        return response

    def initialize(self) -> dict[str, Any]:
        """Perform the MCP initialize handshake.

        Sends the required client info and protocol version, then
        confirms with a ``notifications/initialized`` notification
        as required by the MCP specification.

        Returns the server capabilities.
        """
        params = {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "agentmax", "version": "0.1.0"},
        }
        response = self._send("initialize", params)
        self._initialized = True
        self._capabilities = response.result.get("capabilities", {})
        # Send the required initialized notification per MCP spec
        self.notify("notifications/initialized")
        return response.result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected).

        Per JSON-RPC 2.0 spec, notifications omit the ``id`` field entirely.
        """
        request = MCPRequest(
            method=method,
            params=params or {},
            id=None,  # None → no id field in JSON (notification)
        )
        self._transport.send_notification(request)

    def list_tools(self) -> list[MCPToolSpec]:
        """Discover available tools from the server."""
        response = self._send("tools/list")
        tools = response.result.get("tools", [])
        # MCP tools often wrap long-running commands. The default timeout (30s)
        # can kill them mid-run; use a generous 600s ceiling — individual MCP
        # servers can shorten via their own protocol if needed.
        return [
            MCPToolSpec(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("inputSchema", {}),
                timeout_seconds=600.0,
            )
            for t in tools
        ]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a tool on the server.

        Returns the result dictionary with ``content`` and ``isError`` fields.
        """
        response = self._send(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return response.result

    def close(self) -> None:
        """Close the transport connection."""
        self._transport.close()

    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


__all__ = ["MCPClient", "MCPToolSpec"]
