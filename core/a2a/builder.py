"""Convenience builder for exposing AgentMax as an A2A server.

Assembles the discovery :class:`AgentCard` and wires an injected message
handler into an :class:`A2AServer`. The handler (what actually processes a
task's text) is provided by the caller — typically the runtime/supervisor — so
this stays decoupled from the agent execution layer. Advertised ``skills`` can
be sourced from ``SkillRegistry.default().names()`` by the caller without
coupling this module to the skills package.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from core.a2a.protocol import AgentCard
from core.a2a.server import A2AServer

DEFAULT_DESCRIPTION = "AgentMax desktop agent — accepts natural-language tasks over A2A."


def build_agent_a2a_server(
    handle: Callable[[str], str],
    *,
    name: str = "AgentMax",
    description: str = DEFAULT_DESCRIPTION,
    url: str = "",
    version: str = "0.1.0",
    capabilities: Iterable[str] | None = None,
    skills: Iterable[str] | None = None,
    auth_token: str | None = None,
) -> A2AServer:
    """Build an :class:`A2AServer` whose card advertises AgentMax's identity.

    ``handle`` receives a task's input text and returns the agent's reply.
    Pass ``auth_token`` to require a bearer token on every request.
    """
    card = AgentCard(
        name=name,
        description=description,
        url=url,
        version=version,
        capabilities=list(capabilities or []),
        skills=list(skills or []),
    )
    return A2AServer(card, handler=handle, auth_token=auth_token)


__all__ = ["build_agent_a2a_server", "DEFAULT_DESCRIPTION"]
