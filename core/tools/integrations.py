"""Wire the MCP / A2A / skill capability managers into the tool layer.

Single place the runtime uses to give a ``ToolExecutor`` access to external MCP
tools, external A2A agents, and bundled skills. Skills are registered into the
tool registry immediately (they need no I/O); the MCP and A2A managers start
empty and connect to external servers/agents lazily at runtime — connecting at
boot would do subprocess/network I/O that could fail and stall startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.a2a.manager import A2AManager
from core.mcp.manager import MCPManager
from core.skills.manager import SkillManager
from core.skills.registry import SkillRegistry
from core.tools.registry import ToolRegistry


@dataclass(slots=True)
class CapabilityManagers:
    """The three capability managers handed to a ToolExecutor."""

    mcp: MCPManager
    a2a: A2AManager
    skills: SkillManager
    registered_skill_ids: list[str] = field(default_factory=list)


def wire_capabilities(
    registry: ToolRegistry,
    *,
    skill_registry: SkillRegistry | None = None,
    register_skills: bool = True,
) -> CapabilityManagers:
    """Build the capability managers and register bundled skills into ``registry``.

    The MCP and A2A managers are returned empty; the runtime connects external
    servers/agents on them later (and calls ``register_into`` to surface those
    tools). Skill tools (``skill.<name>``) are registered up front.
    """
    skills = SkillManager(skill_registry or SkillRegistry.default())
    registered: list[str] = skills.register_into(registry) if register_skills else []
    return CapabilityManagers(
        mcp=MCPManager(),
        a2a=A2AManager(),
        skills=skills,
        registered_skill_ids=registered,
    )


__all__ = ["CapabilityManagers", "wire_capabilities"]
