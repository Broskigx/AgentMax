"""MCPManager — connect to MCP servers and bridge their tools into AgentMax.

This is the integration glue between the portable MCP client layer and
AgentMax's tool registry/executor. It owns named server connections, discovers
their tools, and turns each remote tool into a ``ToolDefinition`` (id
``mcp.<server>.<tool>``) so planners and the executor see them like any other
tool. External MCP tools default to ``medium`` risk since they run code the
host does not control.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.mcp.client import MCPClient, MCPToolSpec
from core.mcp.transport import StdioTransport, StreamableHTTPTransport
from core.tools.models import ToolDefinition

if TYPE_CHECKING:
    from core.mcp.transport import MCPTransport
    from core.tools.registry import ToolRegistry

TOOL_ID_PREFIX = "mcp"


def _tool_id(server: str, tool_name: str) -> str:
    return f"{TOOL_ID_PREFIX}.{server}.{tool_name}"


def extract_text(result: dict[str, Any]) -> str:
    """Flatten an MCP ``tools/call`` result into a single text string."""
    blocks = result.get("content", [])
    parts = [
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p)


class MCPManager:
    """Owns MCP server connections and bridges their tools to the registry."""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        # tool_id -> (server, remote_tool_name)
        self._tool_index: dict[str, tuple[str, str]] = {}

    # -- connection -------------------------------------------------------

    def connect(self, server: str, transport: MCPTransport) -> MCPClient:
        """Connect a named server over an arbitrary transport and handshake."""
        if server in self._clients:
            raise ValueError(f"MCP server already connected: {server}")
        client = MCPClient(transport)
        client.initialize()
        self._clients[server] = client
        return client

    def connect_stdio(self, server: str, command: list[str]) -> MCPClient:
        """Connect to a subprocess MCP server over stdio."""
        return self.connect(server, StdioTransport(command))

    def connect_http(self, server: str, url: str) -> MCPClient:
        """Connect to an MCP server over Streamable HTTP."""
        return self.connect(server, StreamableHTTPTransport(url))

    def servers(self) -> list[str]:
        """Return the names of connected servers."""
        return list(self._clients)

    def _client(self, server: str) -> MCPClient:
        try:
            return self._clients[server]
        except KeyError as exc:
            raise KeyError(f"Unknown MCP server: {server}") from exc

    # -- discovery / calling ---------------------------------------------

    def list_tools(self, server: str) -> list[MCPToolSpec]:
        """List the tools advertised by a connected server."""
        return self._client(server).list_tools()

    def call_tool(
        self, server: str, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call ``name`` on ``server`` and return the raw MCP result dict."""
        return self._client(server).call_tool(name, arguments or {})

    def call_by_id(self, tool_id: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a tool by its registry id (``mcp.<server>.<tool>``)."""
        try:
            server, name = self._tool_index[tool_id]
        except KeyError as exc:
            raise KeyError(f"Unknown MCP tool id: {tool_id}") from exc
        return self.call_tool(server, name, arguments)

    # -- registry bridge --------------------------------------------------

    def tool_definitions(self, server: str) -> list[ToolDefinition]:
        """Build ``ToolDefinition`` objects for every tool on ``server``."""
        definitions: list[ToolDefinition] = []
        for spec in self.list_tools(server):
            tool_id = _tool_id(server, spec.name)
            self._tool_index[tool_id] = (server, spec.name)
            definitions.append(
                ToolDefinition.from_dict(
                    {
                        "id": tool_id,
                        "name": spec.name,
                        "description": spec.description,
                        "category": "mcp",
                        "risk_level": "medium",
                        "permissions": ["network"],
                        "input_schema": spec.parameters or {"type": "object", "properties": {}},
                        "output_schema": {"type": "object", "properties": {}},
                        "timeout_ms": int(spec.timeout_seconds * 1000),
                        "execution_mode": "async",
                        "requires_user_idle": False,
                    }
                )
            )
        return definitions

    def register_into(self, registry: ToolRegistry, server: str) -> list[str]:
        """Register a server's tools into ``registry``; return the ids added.

        Already-present ids are skipped so this is safe to call repeatedly.
        """
        added: list[str] = []
        for definition in self.tool_definitions(server):
            if registry.maybe_get(definition.id) is None:
                registry.register(definition)
                added.append(definition.id)
        return added

    # -- teardown ---------------------------------------------------------

    def close(self, server: str) -> None:
        """Close and forget a single server connection."""
        client = self._clients.pop(server, None)
        if client is not None:
            client.close()
        self._tool_index = {
            tid: pair for tid, pair in self._tool_index.items() if pair[0] != server
        }

    def close_all(self) -> None:
        """Close every connection."""
        for client in self._clients.values():
            client.close()
        self._clients.clear()
        self._tool_index.clear()


__all__ = ["MCPManager", "extract_text"]
