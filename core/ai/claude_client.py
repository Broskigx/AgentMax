"""Claude API client -- vision, reasoning, and structured tool use with prompt caching."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from core.ai.base_client import BaseAIClient
from core.ai.circuit_breaker import CircuitBreaker, CircuitOpenError, get_claude_circuit_breaker

log = structlog.get_logger(__name__)


class ClaudeClient(BaseAIClient):
    """
    Anthropic Claude client for AgentMax.

    Features:
      - Vision queries with screenshots (claude-sonnet-4-6)
      - Structured JSON responses
      - Prompt caching (cache_control) for system prompts
      - Exponential backoff retry
      - Token usage tracking
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._client: Any = None
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._call_count = 0
        self._cb: CircuitBreaker = get_claude_circuit_breaker()

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=self._config.anthropic_api_key)
        return self._client

    async def vision_query(
        self,
        system: str,
        prompt: str,
        image_b64: str,
        max_tokens: int | None = None,
    ) -> str:
        client = self._get_client()
        max_tok = max_tokens or self._config.max_tokens
        vision_model = getattr(self._config, "effective_vision_model", self._config.model)

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        max_retries = getattr(self._config, "max_retries", 3)
        retry_delay = getattr(self._config, "retry_delay_sec", 1.0)

        for attempt in range(max_retries):
            try:
                async with await self._cb.guard("claude-vision"):
                    t0 = time.perf_counter()
                    response = await client.messages.create(
                        model=vision_model,
                        max_tokens=max_tok,
                        system=[
                            {
                                "type": "text",
                                "text": system,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                        messages=messages,
                    )
                elapsed = (time.perf_counter() - t0) * 1000
                self._track_usage(response)
                self._call_count += 1
                log.debug(
                    "claude.vision_query", ms=f"{elapsed:.0f}", tokens=response.usage.output_tokens
                )
                return response.content[0].text
            except CircuitOpenError as exc:
                log.warning("claude.circuit_open", error=str(exc))
                raise
            except Exception as exc:
                if attempt == max_retries - 1:
                    log.error("claude.vision_query_failed", error=str(exc))
                    raise
                delay = retry_delay * (2**attempt)
                log.warning("claude.retry", attempt=attempt + 1, delay=delay, error=str(exc))
                await asyncio.sleep(delay)

        return ""

    async def text_query(
        self,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
    ) -> str:
        client = self._get_client()
        max_tok = max_tokens or self._config.max_tokens

        max_retries = getattr(self._config, "max_retries", 3)
        retry_delay = getattr(self._config, "retry_delay_sec", 1.0)

        for attempt in range(max_retries):
            try:
                response = await client.messages.create(
                    model=self._config.model,
                    max_tokens=max_tok,
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": prompt}],
                )
                self._track_usage(response)
                return response.content[0].text
            except Exception:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(retry_delay * (2**attempt))

        return ""

    async def chat_query(
        self,
        system: str,
        history: list[dict],
        *,
        image_b64: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Multi-turn conversation with full message history and optional vision."""
        client = self._get_client()
        max_tok = max_tokens or self._config.max_tokens

        messages = list(history)

        # Attach image to the last user turn if provided
        if image_b64 and messages and messages[-1]["role"] == "user":
            last_text = messages[-1]["content"]
            messages[-1] = {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": last_text},
                ],
            }

        vision_model = getattr(self._config, "effective_vision_model", self._config.model)
        model = vision_model if image_b64 else self._config.model
        max_retries = getattr(self._config, "max_retries", 3)
        retry_delay = getattr(self._config, "retry_delay_sec", 1.0)

        for attempt in range(max_retries):
            try:
                t0 = time.perf_counter()
                response = await client.messages.create(
                    model=model,
                    max_tokens=max_tok,
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=messages,
                )
                elapsed = (time.perf_counter() - t0) * 1000
                self._track_usage(response)
                self._call_count += 1
                log.debug(
                    "claude.chat_query",
                    ms=f"{elapsed:.0f}",
                    tokens=response.usage.output_tokens,
                    turns=len(history),
                )
                return response.content[0].text
            except Exception as exc:
                if attempt == max_retries - 1:
                    log.error("claude.chat_query_failed", error=str(exc))
                    raise
                delay = retry_delay * (2**attempt)
                await asyncio.sleep(delay)

        return ""

    async def query_with_tools(
        self,
        system: str,
        prompt: str,
        tools: list[dict],
        image_b64: str | None = None,
    ) -> dict:
        """Tool-use query -- returns {text, tool_calls}."""
        client = self._get_client()

        content: list[dict] = []
        if image_b64:
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
                }
            )
        content.append({"type": "text", "text": prompt})

        response = await client.messages.create(
            model=self._config.vision_model,
            max_tokens=self._config.max_tokens,
            system=system,
            tools=tools,
            messages=[{"role": "user", "content": content}],
        )
        self._track_usage(response)

        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        tool_calls = [
            {"name": b.name, "input": b.input} for b in response.content if b.type == "tool_use"
        ]

        return {"text": " ".join(text_parts), "tool_calls": tool_calls}

    def _track_usage(self, response: Any) -> None:
        try:
            self._total_input_tokens += response.usage.input_tokens
            self._total_output_tokens += response.usage.output_tokens
        except Exception:
            pass

    @property
    def backend_name(self) -> str:
        return "claude"

    @property
    def model_name(self) -> str:
        return self._config.model

    @property
    def usage_stats(self) -> dict[str, int]:
        return {
            "calls": self._call_count,
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "backend": "claude",
        }
