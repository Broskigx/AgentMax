"""SkillManager — expose skills as ``skill.<name>`` tools in the registry.

Mirrors MCPManager / A2AManager: it owns a :class:`SkillRegistry`, registers
each user-invocable skill as a ``ToolDefinition`` (id ``skill.<name>``) in the
tool registry, and runs a skill's pipeline through an injected ``run_tool``
callable (the ToolExecutor wires its own, threading every step back through the
full execution pipeline).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.skills.executor import SkillExecutor, ToolRunner
from core.skills.registry import SkillRegistry
from core.skills.translator import ToolTranslator
from core.skills.types import SkillResult
from core.tools.models import ToolDefinition

if TYPE_CHECKING:
    from core.tools.registry import ToolRegistry

TOOL_ID_PREFIX = "skill"


def _tool_id(name: str) -> str:
    return f"{TOOL_ID_PREFIX}.{name}"


class SkillManager:
    """Owns the skill catalog and bridges skills into the tool registry."""

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        *,
        translator: ToolTranslator | None = None,
    ) -> None:
        self._registry = registry or SkillRegistry.default()
        self._translator = translator or ToolTranslator()
        # tool_id -> skill name
        self._tool_index: dict[str, str] = {}

    # -- registry bridge --------------------------------------------------

    def tool_definitions(self) -> list[ToolDefinition]:
        """Build a ``ToolDefinition`` for every user-invocable skill."""
        definitions: list[ToolDefinition] = []
        for skill in self._registry.list(user_invocable_only=True):
            tool_id = _tool_id(skill.name)
            self._tool_index[tool_id] = skill.name
            definitions.append(
                ToolDefinition.from_dict(
                    {
                        "id": tool_id,
                        "name": skill.name,
                        "description": skill.description or f"Skill: {skill.name}",
                        "category": "skill",
                        "risk_level": "medium",
                        "permissions": [],
                        "input_schema": {"type": "object", "properties": {}},
                        "output_schema": {"type": "object", "properties": {}},
                        "timeout_ms": 120_000,
                        "execution_mode": "async",
                        "requires_user_idle": False,
                    }
                )
            )
        return definitions

    def register_into(self, registry: ToolRegistry) -> list[str]:
        """Register every skill into ``registry``; return the ids added.

        Already-present ids are skipped so this is safe to call repeatedly.
        """
        added: list[str] = []
        for definition in self.tool_definitions():
            if registry.maybe_get(definition.id) is None:
                registry.register(definition)
                added.append(definition.id)
        return added

    # -- execution --------------------------------------------------------

    def run_skill(
        self,
        tool_id: str,
        inputs: dict[str, Any] | None,
        run_tool: ToolRunner,
    ) -> SkillResult:
        """Run the skill behind ``tool_id`` using ``run_tool`` for each step."""
        try:
            name = self._tool_index[tool_id]
        except KeyError as exc:
            raise KeyError(f"Unknown skill tool id: {tool_id}") from exc
        skill = self._registry.get(name)
        executor = SkillExecutor(run_tool, translator=self._translator)
        return executor.run(skill, inputs or {})


__all__ = ["SkillManager"]
