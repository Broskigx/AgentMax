"""AgentMax Plugin SDK -- base class for third-party plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class PluginManifest:
    name: str
    version: str
    description: str
    author: str
    capabilities: list[str]
    requires_permissions: list[str]


class AgentMaxPlugin(ABC):
    """
    Base class for AgentMax plugins.

    Plugins extend the agent's capabilities with new skills,
    integrations, or UI components.

    Life cycle:
      on_load()   -- called once when plugin is registered
      on_task()   -- called when a task matches the plugin's domain
      on_unload() -- called when plugin is removed
    """

    @property
    @abstractmethod
    def manifest(self) -> PluginManifest: ...

    async def on_load(self, context: Any) -> None:
        """Called when the plugin is loaded. Store context for later use."""
        self._ctx = context

    async def on_task(self, description: str, context: Any) -> bool:
        """
        Called for each task. Return True if this plugin handles it.
        Return False to pass to the standard pipeline.
        """
        return False

    async def on_unload(self) -> None:
        """Called when the plugin is removed."""
        pass

    def matches(self, description: str) -> bool:
        """Quick keyword check -- override for more sophisticated matching."""
        return False


class PluginRegistry:
    """Manages loaded plugins."""

    def __init__(self) -> None:
        self._plugins: list[AgentMaxPlugin] = []

    async def register(self, plugin: AgentMaxPlugin, context: Any) -> None:
        await plugin.on_load(context)
        self._plugins.append(plugin)

    async def unregister(self, name: str) -> None:
        for i, p in enumerate(self._plugins):
            if p.manifest.name == name:
                await p.on_unload()
                self._plugins.pop(i)
                break

    async def route_task(self, description: str, context: Any) -> bool:
        for plugin in self._plugins:
            if plugin.matches(description):
                return await plugin.on_task(description, context)
        return False

    @property
    def plugins(self) -> list[PluginManifest]:
        return [p.manifest for p in self._plugins]
