"""Tests for the MCP/A2A server endpoint cores (core/server_endpoints.py).

These cover the injectable, runtime-independent logic. The live IPC glue
(real supervisor submit/poll and executor-backed run_tool) needs end-to-end
verification against a running app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from core.mcp import build_tool_mcp_server
from core.server_endpoints import agent_card_dict, mcp_response, run_a2a_task
from core.tools.models import ToolDefinition
from core.tools.registry import ToolRegistry


async def _nosleep(_seconds: float) -> None:
    return None


# ---------------------------------------------------------------------------
# agent card
# ---------------------------------------------------------------------------


def test_agent_card_dict() -> None:
    card = agent_card_dict(skills=["file-organizer"], capabilities=["chat"], url="http://x")
    assert card["name"] == "AgentMax"
    assert card["skills"] == ["file-organizer"]
    assert card["capabilities"] == ["chat"]
    assert card["url"] == "http://x"


# ---------------------------------------------------------------------------
# A2A task runner (submit + poll)
# ---------------------------------------------------------------------------


async def test_run_a2a_task_returns_summary_on_completion() -> None:
    statuses = iter([{"status": "EXECUTING"}, {"status": "COMPLETED", "summary": "done"}])

    async def submit(_text: str) -> str:
        return "t1"

    async def poll(_task_id: str) -> dict:
        return next(statuses)

    out = await run_a2a_task("hi", submit=submit, poll=poll, sleep=_nosleep)
    assert out == "done"


async def test_run_a2a_task_empty_summary_falls_back() -> None:
    async def submit(_text: str) -> str:
        return "t1"

    async def poll(_task_id: str) -> dict:
        return {"status": "COMPLETED", "summary": ""}

    assert await run_a2a_task("hi", submit=submit, poll=poll, sleep=_nosleep) == "(no output)"


async def test_run_a2a_task_raises_on_failure() -> None:
    async def submit(_text: str) -> str:
        return "t1"

    async def poll(_task_id: str) -> dict:
        return {"status": "FAILED", "error": "boom"}

    with pytest.raises(RuntimeError, match="boom"):
        await run_a2a_task("hi", submit=submit, poll=poll, sleep=_nosleep)


async def test_run_a2a_task_times_out() -> None:
    clock_values = iter([0.0, 2.0])  # deadline = 1.0; second read (2.0) exceeds it

    async def submit(_text: str) -> str:
        return "t1"

    async def poll(_task_id: str) -> dict:
        return {"status": "EXECUTING"}

    with pytest.raises(TimeoutError):
        await run_a2a_task(
            "hi",
            submit=submit,
            poll=poll,
            timeout=1.0,
            sleep=_nosleep,
            clock=lambda: next(clock_values),
        )


# ---------------------------------------------------------------------------
# MCP request dispatch
# ---------------------------------------------------------------------------


@dataclass
class _ToolRunResult:
    success: bool = True
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _mcp_server():
    registry = ToolRegistry(
        [
            ToolDefinition.from_dict(
                {
                    "id": "demo.x",
                    "description": "demo",
                    "category": "screen",
                    "input_schema": {"type": "object", "properties": {}},
                }
            )
        ]
    )
    return build_tool_mcp_server(registry, lambda tid, args: _ToolRunResult(True, {"text": f"ran {tid}"}))


def test_mcp_response_initialize_list_call() -> None:
    server = _mcp_server()

    init = mcp_response(server, {"method": "initialize", "id": 1})
    assert init["result"]["serverInfo"]["name"] == "agentmax"

    listed = mcp_response(server, {"method": "tools/list", "id": 2})
    assert {t["name"] for t in listed["result"]["tools"]} == {"demo.x"}

    called = mcp_response(
        server, {"method": "tools/call", "id": 3, "params": {"name": "demo.x", "arguments": {}}}
    )
    assert called["result"]["isError"] is False
    assert called["result"]["content"][0]["text"] == "ran demo.x"


# ---------------------------------------------------------------------------
# IPC glue (core/ipc.py): agent card config + A2A task state across requests
# ---------------------------------------------------------------------------


class _ServerCfg:
    host = "192.168.1.50"
    api_port = 7791


class _Cfg:
    server = _ServerCfg()


def _bare_ipc(agent_pool: dict[str, Any] | None = None):
    from core.ipc import IPCServer

    server = IPCServer.__new__(IPCServer)
    server._metrics = {
        "request_count": 0,
        "error_count": 0,
        "avg_latency_ms": 0.0,
        "auth_rejected": 0,
    }
    server._agent_pool = agent_pool or {}
    server._config = _Cfg()
    return server


def test_agent_card_uses_server_config_host_and_port() -> None:
    card = _bare_ipc()._a2a_agent_card()
    assert card["url"] == "http://192.168.1.50:7791"


class _FakeSupervisor:
    async def submit_task(self, request: Any) -> str:
        return "sup-task-1"

    def get_task_result(self, task_id: str) -> dict[str, Any]:
        return {"id": task_id, "status": "COMPLETED", "summary": "hecho", "error": None}


async def test_a2a_task_state_survives_across_requests() -> None:
    ipc = _bare_ipc({"supervisor": _FakeSupervisor()})

    sent = await ipc.handle_rest(
        "POST",
        "/a2a/tasks",
        {"jsonrpc": "2.0", "id": "r1", "method": "tasks/send", "params": {"input": "hola"}},
    )
    assert sent["result"]["state"] == "completed"
    assert sent["result"]["output"] == "hecho"

    # A second request (tasks/get) must see the task created by the first one —
    # a per-request A2AServer instance would report "Task not found" here.
    got = await ipc.handle_rest(
        "POST",
        "/a2a/tasks",
        {
            "jsonrpc": "2.0",
            "id": "r2",
            "method": "tasks/get",
            "params": {"id": sent["result"]["id"]},
        },
    )
    assert got["result"]["id"] == sent["result"]["id"]
