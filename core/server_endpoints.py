"""Outward-facing MCP / A2A server endpoint adapters.

Wired into ``core/ipc.py``'s REST dispatcher to expose AgentMax as an MCP tool
server and an A2A agent. The execution-facing dependencies (task submit/poll for
A2A, tool execution for MCP) are injected, so the logic here is unit-testable;
the IPC supplies the live Supervisor/executor wiring.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from core.a2a.builder import DEFAULT_DESCRIPTION
from core.a2a.protocol import AgentCard
from core.mcp.protocol import MCPRequest
from core.mcp.server import MCPServer

# Terminal supervisor task states (TaskStatus enum names).
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}

TaskSubmit = Callable[[str], Awaitable[str]]
TaskPoll = Callable[[str], Awaitable[dict[str, Any] | None]]


def agent_card_dict(
    *,
    name: str = "AgentMax",
    description: str = DEFAULT_DESCRIPTION,
    url: str = "",
    skills: list[str] | None = None,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Build the A2A discovery card served at ``/.well-known/agent.json``."""
    return AgentCard(
        name=name,
        description=description,
        url=url,
        skills=list(skills or []),
        capabilities=list(capabilities or []),
    ).to_dict()


async def run_a2a_task(
    description: str,
    *,
    submit: TaskSubmit,
    poll: TaskPoll,
    timeout: float = 120.0,
    interval: float = 0.25,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Submit a task, poll until it reaches a terminal state, and return its text.

    Raises ``RuntimeError`` if the task fails/cancels and ``TimeoutError`` if it
    does not finish within ``timeout`` — the A2A server maps a raising handler to
    a FAILED task.
    """
    task_id = await submit(description)
    deadline = clock() + timeout
    while True:
        status = await poll(task_id) or {}
        state = status.get("status")
        if state == "COMPLETED":
            return (status.get("summary") or "").strip() or "(no output)"
        if state in {"FAILED", "CANCELLED"}:
            raise RuntimeError(status.get("error") or f"Task {str(state).lower()}")
        if clock() >= deadline:
            raise TimeoutError(f"A2A task did not complete within {timeout:g}s")
        await sleep(interval)


def is_terminal(status_name: str) -> bool:
    """Whether a supervisor TaskStatus name is terminal."""
    return status_name in _TERMINAL


def mcp_response(server: MCPServer, data: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a raw JSON-RPC MCP request dict through ``server`` to a dict.

    Note: ``server.handle`` runs tool handlers synchronously, so when those
    handlers bridge to async execution this must be called inside a worker
    thread (``asyncio.to_thread``) to avoid blocking the event loop.
    """
    request = MCPRequest(
        method=str(data.get("method", "")),
        params=dict(data.get("params") or {}),
        id=data.get("id", 0),
    )
    return json.loads(server.handle(request).to_json())


__all__ = ["agent_card_dict", "run_a2a_task", "is_terminal", "mcp_response", "TaskSubmit", "TaskPoll"]
