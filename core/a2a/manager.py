"""A2AManager — connect to external A2A agents and bridge them into AgentMax.

The AgentMax equivalent of OpenJarvis's ``A2AAgentTool``: instead of subclassing
a framework ``BaseTool``, each remote agent is exposed as a ``ToolDefinition``
(id ``a2a.<agent>``) registered in the existing ``ToolRegistry``. Calling the
tool sends a single task to the remote agent and returns its output. Remote
agents default to ``medium`` risk since they run code the host does not control.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

from core.a2a.client import A2AClient
from core.a2a.protocol import A2ATask, AgentCard
from core.tools.models import ToolDefinition

if TYPE_CHECKING:
    from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

TOOL_ID_PREFIX = "a2a"
_SUCCESS_STATES = {"completed", "working"}


class A2AAgentClient(Protocol):
    """Minimal client surface the manager depends on (eases testing)."""

    def discover(self) -> AgentCard: ...

    def send_task(self, input_text: str, **kwargs: Any) -> A2ATask: ...


def _tool_id(name: str) -> str:
    return f"{TOOL_ID_PREFIX}.{name}"


class A2AManager:
    """Owns external A2A agent connections and bridges them to the registry."""

    def __init__(self) -> None:
        self._clients: dict[str, A2AAgentClient] = {}
        # tool_id -> agent name
        self._tool_index: dict[str, str] = {}

    # -- connection -------------------------------------------------------

    def register_agent(self, name: str, client: A2AAgentClient) -> A2AAgentClient:
        """Register a pre-built client (any object with discover/send_task)."""
        if name in self._clients:
            raise ValueError(f"A2A agent already registered: {name}")
        self._clients[name] = client
        return client

    def connect(self, name: str, base_url: str, *, timeout: float = 30.0) -> A2AAgentClient:
        """Connect to an external A2A agent at ``base_url``."""
        return self.register_agent(name, A2AClient(base_url, timeout=timeout))

    def agents(self) -> list[str]:
        """Return the names of registered agents."""
        return list(self._clients)

    def _client(self, name: str) -> A2AAgentClient:
        try:
            return self._clients[name]
        except KeyError as exc:
            raise KeyError(f"Unknown A2A agent: {name}") from exc

    # -- registry bridge --------------------------------------------------

    def tool_definitions(self, name: str) -> list[ToolDefinition]:
        """Build a ``ToolDefinition`` for the agent (best-effort discovery)."""
        client = self._client(name)
        description = f"External A2A agent: {name}"
        try:
            card = client.discover()
            if card.description:
                description = card.description
        except Exception as exc:  # noqa: BLE001 - offline agents still get registered
            logger.debug("A2A discovery failed for %s: %s", name, exc)

        tool_id = _tool_id(name)
        self._tool_index[tool_id] = name
        return [
            ToolDefinition.from_dict(
                {
                    "id": tool_id,
                    "name": tool_id,
                    "description": description,
                    "category": "a2a",
                    "risk_level": "medium",
                    "permissions": ["network"],
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "input": {
                                "type": "string",
                                "description": "Input text to send to the remote agent.",
                            }
                        },
                        "required": ["input"],
                    },
                    "output_schema": {"type": "object", "properties": {}},
                    "timeout_ms": int(getattr(client, "_timeout", 30.0) * 1000),
                    "execution_mode": "async",
                    "requires_user_idle": False,
                }
            )
        ]

    def register_into(self, registry: ToolRegistry, name: str) -> list[str]:
        """Register an agent into ``registry``; return the ids added.

        Already-present ids are skipped so this is safe to call repeatedly.
        """
        added: list[str] = []
        for definition in self.tool_definitions(name):
            if registry.maybe_get(definition.id) is None:
                registry.register(definition)
                added.append(definition.id)
        return added

    # -- calling ----------------------------------------------------------

    def call_by_id(self, tool_id: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call an agent by its registry id (``a2a.<agent>``)."""
        try:
            name = self._tool_index[tool_id]
        except KeyError as exc:
            raise KeyError(f"Unknown A2A tool id: {tool_id}") from exc
        input_text = str((arguments or {}).get("input", ""))
        task = self._client(name).send_task(input_text)
        state = task.state_value
        return {
            "task_id": task.task_id,
            "state": state,
            "output": task.output_text,
            "success": state in _SUCCESS_STATES,
        }

    # -- teardown ---------------------------------------------------------

    def close(self, name: str) -> None:
        """Forget a single agent connection."""
        self._clients.pop(name, None)
        self._tool_index = {tid: n for tid, n in self._tool_index.items() if n != name}

    def close_all(self) -> None:
        """Forget every connection."""
        self._clients.clear()
        self._tool_index.clear()


__all__ = ["A2AManager", "A2AAgentClient"]
