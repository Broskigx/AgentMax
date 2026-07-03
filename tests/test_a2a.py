"""Tests for the A2A (Agent-to-Agent) layer and registry bridge.

Covers:
  - protocol message round-trips and task-state normalization
  - in-process A2A server: task lifecycle, errors, bearer auth, event callback
  - A2AManager bridging a remote agent into a ToolRegistry
  - ToolExecutor routing of ``a2a.*`` tool ids to the manager
"""

from __future__ import annotations

from typing import Any

from core.a2a import (
    A2AClient,
    A2AManager,
    A2ARequest,
    A2AResponse,
    A2AServer,
    A2ATask,
    AgentCard,
    TaskState,
    build_agent_a2a_server,
)
from core.tools.executor import ToolExecutor
from core.tools.models import ToolExecutionContext, ToolRequest
from core.tools.registry import ToolRegistry


class _LoopbackA2AClient:
    """In-process client that drives an :class:`A2AServer` without HTTP."""

    def __init__(self, server: A2AServer, *, timeout: float = 30.0) -> None:
        self._server = server
        self._timeout = timeout

    def discover(self) -> AgentCard:
        return self._server.agent_card

    def send_task(self, input_text: str, **kwargs: Any) -> A2ATask:
        resp = self._server.handle_request(
            A2ARequest("tasks/send", {"message": {"parts": [{"text": input_text}]}}).to_dict()
        )
        r = resp["result"]
        return A2ATask(task_id=r["id"], state=r["state"], output_text=r["output"])


def _server(**kwargs: Any) -> A2AServer:
    card = AgentCard(name="Helper", description="A helpful test agent.")
    return A2AServer(card, handler=lambda text: f"handled: {text}", **kwargs)


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


def test_request_to_dict_is_jsonrpc() -> None:
    d = A2ARequest("tasks/send", {"x": 1}).to_dict()
    assert d["jsonrpc"] == "2.0"
    assert d["method"] == "tasks/send"
    assert "id" in d


def test_response_round_trip() -> None:
    restored = A2AResponse.from_json(A2AResponse(result={"ok": True}, request_id="9").to_json())
    assert restored.result == {"ok": True}
    assert restored.request_id == "9"


def test_task_state_value_accepts_enum_and_str() -> None:
    assert A2ATask(state=TaskState.COMPLETED).state_value == "completed"
    assert A2ATask(state="working").state_value == "working"
    assert A2ATask(state="completed").to_dict()["state"] == "completed"


def test_agent_card_to_dict() -> None:
    card = AgentCard(name="X", capabilities=["chat"]).to_dict()
    assert card["name"] == "X"
    assert card["capabilities"] == ["chat"]


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------


def test_task_send_executes_handler() -> None:
    resp = _server().handle_request(
        A2ARequest("tasks/send", {"message": {"parts": [{"text": "ping"}]}}).to_dict()
    )
    assert resp["result"]["state"] == "completed"
    assert resp["result"]["output"] == "handled: ping"


def test_task_get_and_cancel() -> None:
    server = _server()
    sent = server.handle_request(A2ARequest("tasks/send", {"input": "go"}).to_dict())
    task_id = sent["result"]["id"]

    got = server.handle_request(A2ARequest("tasks/get", {"id": task_id}).to_dict())
    assert got["result"]["id"] == task_id

    cancelled = server.handle_request(A2ARequest("tasks/cancel", {"id": task_id}).to_dict())
    assert cancelled["result"]["state"] == "canceled"


def test_task_retention_is_capped() -> None:
    server = _server(max_tasks=2)
    ids = []
    for i in range(3):
        resp = server.handle_request(A2ARequest("tasks/send", {"input": f"t{i}"}).to_dict())
        ids.append(resp["result"]["id"])

    # The oldest task was evicted; the two newest remain reachable.
    oldest = server.handle_request(A2ARequest("tasks/get", {"id": ids[0]}).to_dict())
    assert oldest["error"]["code"] == -32602
    for task_id in ids[1:]:
        got = server.handle_request(A2ARequest("tasks/get", {"id": task_id}).to_dict())
        assert got["result"]["id"] == task_id


def test_unknown_method_and_missing_task_error() -> None:
    server = _server()
    bad_method = server.handle_request(A2ARequest("tasks/explode", {}).to_dict())
    assert bad_method["error"]["code"] == -32601
    missing = server.handle_request(A2ARequest("tasks/get", {"id": "nope"}).to_dict())
    assert missing["error"]["code"] == -32602


def test_bearer_auth_rejects_missing_or_wrong_token() -> None:
    server = _server(auth_token="s3cret")
    req = A2ARequest("tasks/send", {"input": "x"}).to_dict()

    assert server.handle_request(req)["error"]["code"] == -32001
    assert server.handle_request(req, token="wrong")["error"]["code"] == -32001
    ok = server.handle_request(req, token="s3cret")
    assert ok["result"]["state"] == "completed"
    # The required scheme is advertised on the card.
    assert server.agent_card.authentication == {"schemes": ["bearer"]}


def test_event_callback_fires() -> None:
    events: list[str] = []
    server = _server(on_event=lambda name, _payload: events.append(name))
    server.handle_request(A2ARequest("tasks/send", {"input": "x"}).to_dict())
    assert events == ["a2a.task_received", "a2a.task_completed"]


# ---------------------------------------------------------------------------
# manager <-> registry bridge
# ---------------------------------------------------------------------------


def test_manager_registers_agent_into_registry() -> None:
    manager = A2AManager()
    manager.register_agent("local", _LoopbackA2AClient(_server()))
    registry = ToolRegistry()
    added = manager.register_into(registry, "local")

    assert added == ["a2a.local"]
    definition = registry.get("a2a.local")
    assert definition.category == "a2a"
    assert definition.description == "A helpful test agent."
    assert manager.register_into(registry, "local") == []  # idempotent


def test_manager_call_by_id() -> None:
    manager = A2AManager()
    manager.register_agent("local", _LoopbackA2AClient(_server()))
    manager.register_into(ToolRegistry(), "local")
    result = manager.call_by_id("a2a.local", {"input": "task"})
    assert result["success"]
    assert result["output"] == "handled: task"


def test_connect_builds_a2a_client() -> None:
    manager = A2AManager()
    client = manager.connect("remote", "http://example.test/agent/")
    assert isinstance(client, A2AClient)
    assert manager.agents() == ["remote"]


# ---------------------------------------------------------------------------
# executor routing
# ---------------------------------------------------------------------------


async def test_executor_routes_a2a_tool_to_manager() -> None:
    manager = A2AManager()
    manager.register_agent("local", _LoopbackA2AClient(_server()))
    registry = ToolRegistry()
    manager.register_into(registry, "local")

    executor = ToolExecutor(registry, a2a_manager=manager)
    result = await executor._run_builtin_tool(
        registry.get("a2a.local"),
        ToolRequest(tool_id="a2a.local", input={"input": "pipe"}),
        ToolExecutionContext(),
    )
    assert result.success
    assert result.output["text"] == "handled: pipe"


async def test_executor_without_a2a_manager_reports_handler_missing() -> None:
    manager = A2AManager()
    manager.register_agent("local", _LoopbackA2AClient(_server()))
    registry = ToolRegistry()
    manager.register_into(registry, "local")

    executor = ToolExecutor(registry)  # no a2a_manager
    result = await executor._run_builtin_tool(
        registry.get("a2a.local"),
        ToolRequest(tool_id="a2a.local", input={"input": "x"}),
        ToolExecutionContext(),
    )
    assert not result.success
    assert result.error_code == "tool.handler_missing"


# ---------------------------------------------------------------------------
# server side: expose AgentMax as an A2A server
# ---------------------------------------------------------------------------


def test_build_agent_a2a_server_card_and_handler() -> None:
    server = build_agent_a2a_server(
        lambda text: f"ok: {text}",
        name="AgentMax",
        skills=["file-organizer"],
        capabilities=["chat"],
    )
    assert server.agent_card.name == "AgentMax"
    assert server.agent_card.skills == ["file-organizer"]
    assert server.agent_card.capabilities == ["chat"]

    resp = server.handle_request(A2ARequest("tasks/send", {"input": "go"}).to_dict())
    assert resp["result"]["output"] == "ok: go"


def test_build_agent_a2a_server_with_auth() -> None:
    server = build_agent_a2a_server(lambda text: text, auth_token="k")
    req = A2ARequest("tasks/send", {"input": "x"}).to_dict()
    assert server.handle_request(req)["error"]["code"] == -32001
    assert server.handle_request(req, token="k")["result"]["state"] == "completed"
