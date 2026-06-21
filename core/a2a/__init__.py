"""A2A (Agent-to-Agent) layer for AgentMax.

Implements the Google A2A protocol so AgentMax can discover and call external
agents (client), expose its own agents to others (:class:`A2AServer`), and
bridge remote agents into the tool registry (:class:`A2AManager`).

Portions adapted from OpenJarvis (Apache-2.0). See core/a2a/NOTICE.
"""

from core.a2a.builder import DEFAULT_DESCRIPTION, build_agent_a2a_server
from core.a2a.client import A2AClient
from core.a2a.manager import A2AAgentClient, A2AManager
from core.a2a.protocol import (
    A2ARequest,
    A2AResponse,
    A2ATask,
    AgentCard,
    TaskState,
)
from core.a2a.server import A2AServer

__all__ = [
    "A2AClient",
    "A2AManager",
    "A2AAgentClient",
    "A2ARequest",
    "A2AResponse",
    "A2ATask",
    "A2AServer",
    "AgentCard",
    "TaskState",
    "build_agent_a2a_server",
    "DEFAULT_DESCRIPTION",
]
