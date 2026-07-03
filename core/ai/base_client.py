"""Abstract AI client -- protocol shared by Claude and LM Studio backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseAIClient(ABC):
    """Unified interface for all AI backends used by AgentMax agents."""

    @abstractmethod
    async def vision_query(
        self,
        system: str,
        prompt: str,
        image_b64: str,
        max_tokens: int | None = None,
    ) -> str:
        """Send a multimodal (text + image) query. Returns the text response."""
        ...

    @abstractmethod
    async def text_query(
        self,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
    ) -> str:
        """Send a text-only query. Returns the text response."""
        ...

    async def chat_query(
        self,
        system: str,
        history: list[dict],
        *,
        image_b64: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """
        Multi-turn conversation query.

        history: Anthropic-format list of {role, content} dicts.
        The last entry must have role=='user'.
        Subclasses should override for native multi-turn support;
        default falls back to text_query using the last user message.
        """
        last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        if image_b64:
            return await self.vision_query(system, last_user, image_b64, max_tokens)
        return await self.text_query(system, last_user, max_tokens)

    @property
    @abstractmethod
    def backend_name(self) -> str: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    def supports_vision(self) -> bool:
        return True

    @property
    def usage_stats(self) -> dict[str, Any]:
        return {}
