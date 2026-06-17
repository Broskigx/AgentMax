"""
AIRouter — routes chat requests to the best available AI backend.

Priority order (configurable):
  1. claude  — Anthropic API (requires ANTHROPIC_API_KEY)
  2. lmstudio — Local LM Studio instance
  3. llamacpp — Local llama.cpp server

Each backend is health-checked on startup and every 60 s.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

_HEALTH_INTERVAL_S = 60.0
_REQUEST_TIMEOUT_S = 120.0


class AIBackend:
    def __init__(self, name: str, url: str, model: str) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.model = model
        self.healthy = False
        self.latency_ms: int | None = None

    async def check_health(self) -> bool:
        try:
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.url}/health")
            self.latency_ms = int((time.monotonic() - t0) * 1000)
            self.healthy = resp.status_code < 400
        except Exception:
            self.healthy = False
            self.latency_ms = None
        return self.healthy

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "model": self.model,
            "healthy": self.healthy,
            "latency_ms": self.latency_ms,
        }


class ClaudeBackend(AIBackend):
    def __init__(self) -> None:
        super().__init__("claude", "https://api.anthropic.com", "claude-sonnet-4-6")

    async def check_health(self) -> bool:
        self.healthy = bool(os.environ.get("ANTHROPIC_API_KEY"))
        return self.healthy

    async def chat(self, messages: list[dict], system: str = "", model: str = "") -> str:
        import anthropic  # noqa: PLC0415
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=model or self.model,
            max_tokens=4096,
            system=system or "You are a helpful AI assistant.",
            messages=messages,
        )
        return resp.content[0].text if resp.content else ""


class OpenAICompatBackend(AIBackend):
    """Handles LM Studio, llama.cpp, and any OpenAI-compatible local server."""

    async def chat(self, messages: list[dict], system: str = "", model: str = "") -> str:
        full_messages = (
            [{"role": "system", "content": system}] if system else []
        ) + messages
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = await client.post(
                f"{self.url}/v1/chat/completions",
                json={
                    "model": model or self.model,
                    "messages": full_messages,
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


class AIRouter:
    def __init__(self) -> None:
        self._backends: list[AIBackend] = []
        self._health_task: asyncio.Task | None = None

    async def start(self, backend_configs: list[dict[str, Any]]) -> None:
        for cfg in backend_configs:
            btype = cfg.get("type", "openai_compat")
            if btype == "claude":
                b: AIBackend = ClaudeBackend()
            else:
                b = OpenAICompatBackend(
                    name=cfg.get("name", "local"),
                    url=cfg.get("url", "http://localhost:1234"),
                    model=cfg.get("model", "local-model"),
                )
            self._backends.append(b)

        # Add Claude if key available and not already in list
        if not any(isinstance(b, ClaudeBackend) for b in self._backends):
            if os.environ.get("ANTHROPIC_API_KEY"):
                self._backends.insert(0, ClaudeBackend())

        await self._check_all()
        self._health_task = asyncio.create_task(self._health_loop(), name="ai-health-loop")

    async def stop(self) -> None:
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

    async def chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        model: str = "",
    ) -> tuple[str, str]:
        """
        Send a chat request to the best available backend.
        Returns (content, backend_name).
        """
        for b in self._backends:
            if not b.healthy:
                continue
            try:
                content = await b.chat(messages, system=system, model=model)
            except Exception as exc:
                log.warning("ai_router.backend_error", backend=b.name, error=str(exc))
                b.healthy = False
            else:
                return content, b.name
        msg = "No AI backends available. Check ANTHROPIC_API_KEY or local model server."
        raise RuntimeError(msg)

    def status(self) -> list[dict[str, Any]]:
        return [b.to_dict() for b in self._backends]

    async def _check_all(self) -> None:
        await asyncio.gather(*[b.check_health() for b in self._backends], return_exceptions=True)

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(_HEALTH_INTERVAL_S)
            await self._check_all()
