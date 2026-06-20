"""Tests for the MCP (Model Context Protocol) client layer and registry bridge.

Covers:
  - JSON-RPC protocol message round-trips (request / response / notification)
  - In-process client <-> generic server handshake, discovery and calls
  - MCPManager bridging remote tools into a ToolRegistry
  - ToolExecutor routing of ``mcp.*`` tool ids to the manager
"""

from __future__ import annotations

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
    extract_text,
)
from core.tools.executor import ToolExecutor
from core.tools.models import ToolExecutionContext, ToolRequest
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
