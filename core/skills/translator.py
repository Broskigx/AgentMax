"""Translate OpenJarvis tool names used in skill steps to AgentMax tool ids.

The ported ``.toml`` skills reference OpenJarvis tool names (``shell_exec``,
``think``, ``file_read`` …). AgentMax exposes the same capabilities under
different ids (``shell.run``, ``reasoning.raw``, ``filesystem.read`` …). This
small data table bridges the two; unmapped names pass through unchanged so a
step referencing a capability AgentMax lacks fails at execution, not at load.
"""

from __future__ import annotations

# OpenJarvis tool name -> AgentMax tool id
TOOL_TRANSLATION: dict[str, str] = {
    "think": "reasoning.raw",
    "shell_exec": "shell.run",
    "file_read": "filesystem.read",
    "file_write": "filesystem.write",
    "web_search": "browser.search",
    "http_request": "browser.read_page",
    "memory_search": "memory.recall",
}


class ToolTranslator:
    """Map skill-step tool names onto AgentMax tool ids."""

    def __init__(self, table: dict[str, str] | None = None) -> None:
        self._table = {**TOOL_TRANSLATION, **(table or {})}

    def translate(self, tool_name: str) -> str:
        """Return the AgentMax tool id for ``tool_name`` (identity if unmapped)."""
        return self._table.get(tool_name, tool_name)

    def is_known(self, tool_name: str) -> bool:
        """Whether ``tool_name`` has an explicit AgentMax mapping."""
        return tool_name in self._table


__all__ = ["TOOL_TRANSLATION", "ToolTranslator"]
