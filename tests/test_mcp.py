"""Tests for the MCP (Model Context Protocol) client layer and registry bridge.

Covers:
  - JSON-RPC protocol message round-trips (request / response / notification)
  - In-process client <-> generic server handshake, discovery and calls
  - MCPManager bridging remote tools into a ToolRegistry
  - ToolExecutor routing of ``mcp.*`` tool ids to the manager
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from core.mcp import (
    InProcessTransport,
    MCPClient,
    MCPError,
    MCPManager,
    MCPNotification,
    MCPRequest,
    MCPResponse,
    MCPServer,
    MCPTool,
    build_tool_mcp_server,
    extract_text,
    tool_to_mcp,
)
from core.tools.executor import ToolExecutor
from core.tools.models import ToolDefinition, ToolExecutionContext, ToolRequest
from core.tools.registry import ToolRegistry


def _echo_server() -> MCPServer:
    return MCPServer(
        tools=[
            MCPTool(
                name="echo",
                handler=lambda args: f"echo: {args.get('text', '')}",
                description="Echo the input text back.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            ),
            MCPTool(
                name="boom",
                handler=lambda args: (_ for _ in ()).throw(RuntimeError("kaboom")),
                description="Always raises.",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


def test_request_round_trip() -> None:
    req = MCPRequest(method="tools/call", params={"name": "echo"}, id=7)
    restored = MCPRequest.from_json(req.to_json())
    assert restored.method == "tools/call"
    assert restored.params == {"name": "echo"}
    assert restored.id == 7


def test_notification_omits_id() -> None:
    # A request with id=None serializes as a JSON-RPC notification.
    note = MCPRequest(method="notifications/initialized", id=None)
    assert "id" not in note.to_dict()
    assert MCPNotification(method="x").to_json()  # smoke


def test_response_error_round_trip() -> None:
    err = MCPResponse.error_response(3, -32601, "nope", data={"k": 1})
    restored = MCPResponse.from_json(err.to_json())
    assert restored.error is not None
    assert restored.error["code"] == -32601
    assert restored.result is None


# ---------------------------------------------------------------------------
# client <-> server (in-process)
# ---------------------------------------------------------------------------


def test_initialize_handshake_and_discovery() -> None:
    client = MCPClient(InProcessTransport(_echo_server()))
    info = client.initialize()
    assert info["serverInfo"]["name"] == "agentmax"
    tools = {t.name for t in client.list_tools()}
    assert tools == {"echo", "boom"}


def test_call_tool_success() -> None:
    client = MCPClient(InProcessTransport(_echo_server()))
    client.initialize()
    result = client.call_tool("echo", {"text": "hi"})
    assert result["isError"] is False
    assert extract_text(result) == "echo: hi"


def test_call_unknown_tool_raises() -> None:
    client = MCPClient(InProcessTransport(_echo_server()))
    client.initialize()
    with pytest.raises(MCPError):
        client.call_tool("does-not-exist", {})


def test_handler_error_surfaces_as_mcp_error() -> None:
    client = MCPClient(InProcessTransport(_echo_server()))
    client.initialize()
    with pytest.raises(MCPError):
        client.call_tool("boom", {})


# ---------------------------------------------------------------------------
# manager <-> registry bridge
# ---------------------------------------------------------------------------


def test_manager_registers_tools_into_registry() -> None:
    manager = MCPManager()
    manager.connect("local", InProcessTransport(_echo_server()))
    registry = ToolRegistry()
    added = manager.register_into(registry, "local")

    assert "mcp.local.echo" in added
    definition = registry.get("mcp.local.echo")
    assert definition.category == "mcp"
    assert definition.risk_level.value == "medium"
    # Re-registering is idempotent (no duplicate-id error, nothing new added).
    assert manager.register_into(registry, "local") == []


def test_manager_call_by_id() -> None:
    manager = MCPManager()
    manager.connect("local", InProcessTransport(_echo_server()))
    manager.register_into(ToolRegistry(), "local")
    result = manager.call_by_id("mcp.local.echo", {"text": "bridge"})
    assert extract_text(result) == "echo: bridge"


# ---------------------------------------------------------------------------
# executor routing
# ---------------------------------------------------------------------------


async def test_executor_routes_mcp_tool_to_manager() -> None:
    manager = MCPManager()
    manager.connect("local", InProcessTransport(_echo_server()))
    registry = ToolRegistry()
    manager.register_into(registry, "local")

    executor = ToolExecutor(registry, mcp_manager=manager)
    definition = registry.get("mcp.local.echo")
    result = await executor._run_builtin_tool(
        definition,
        ToolRequest(tool_id="mcp.local.echo", input={"text": "pipe"}),
        ToolExecutionContext(),
    )
    assert result.success
    assert result.output["text"] == "echo: pipe"


async def test_executor_without_manager_reports_handler_missing() -> None:
    manager = MCPManager()
    manager.connect("local", InProcessTransport(_echo_server()))
    registry = ToolRegistry()
    manager.register_into(registry, "local")

    executor = ToolExecutor(registry)  # no mcp_manager
    definition = registry.get("mcp.local.echo")
    result = await executor._run_builtin_tool(
        definition,
        ToolRequest(tool_id="mcp.local.echo", input={"text": "x"}),
        ToolExecutionContext(),
    )
    assert not result.success
    assert result.error_code == "tool.handler_missing"


# ---------------------------------------------------------------------------
# server side: expose AgentMax tools as an MCP server
# ---------------------------------------------------------------------------


@dataclass
class _ToolRunResult:
    success: bool = True
    output: dict = field(default_factory=dict)
    error: str | None = None


def _tool_catalog() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDefinition.from_dict(
                {
                    "id": "demo.read",
                    "description": "read something",
                    "category": "screen",
                    "risk_level": "low",
                    "permissions": ["read"],
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
                }
            ),
            ToolDefinition.from_dict(
                {
                    "id": "demo.write",
                    "description": "write something",
                    "category": "filesystem",
                    "risk_level": "high",
                    "permissions": ["write"],
                }
            ),
        ]
    )


def test_build_tool_mcp_server_exposes_registry() -> None:
    def runner(tool_id: str, args: dict) -> _ToolRunResult:
        return _ToolRunResult(success=True, output={"text": f"ran {tool_id}"})

    server = build_tool_mcp_server(_tool_catalog(), runner)
    client = MCPClient(InProcessTransport(server))
    client.initialize()

    assert {t.name for t in client.list_tools()} == {"demo.read", "demo.write"}
    result = client.call_tool("demo.read", {"q": "hi"})
    assert extract_text(result) == "ran demo.read"


def test_exposed_tool_annotations_from_risk() -> None:
    catalog = _tool_catalog()

    def runner(tool_id: str, args: dict) -> _ToolRunResult:
        return _ToolRunResult()

    read_tool = tool_to_mcp(catalog.get("demo.read"), runner)
    write_tool = tool_to_mcp(catalog.get("demo.write"), runner)
    assert read_tool.annotations == {"readOnlyHint": True, "destructiveHint": False}
    assert write_tool.annotations == {"readOnlyHint": False, "destructiveHint": True}


def test_exposed_tool_failure_surfaces_as_error() -> None:
    def failing(tool_id: str, args: dict) -> _ToolRunResult:
        return _ToolRunResult(success=False, error="nope")

    server = build_tool_mcp_server(_tool_catalog(), failing)
    client = MCPClient(InProcessTransport(server))
    client.initialize()
    with pytest.raises(MCPError):
        client.call_tool("demo.read", {})
