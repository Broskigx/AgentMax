"""AI Router -- selects cloud or local model backends, unified interface."""

from __future__ import annotations

from typing import Any

import structlog

from core.ai.base_client import BaseAIClient
from core.utils.resilience import CircuitBreaker, retry_async

log = structlog.get_logger(__name__)

# Separate breakers for cloud vs. local: local backend failures must not poison
# the cloud API breaker, and vice versa.
_cloud_breaker = CircuitBreaker(fail_threshold=5, reset_timeout=60.0, name="claude-api")
_local_breaker = CircuitBreaker(fail_threshold=3, reset_timeout=30.0, name="local-model")


class AIRouter(BaseAIClient):
    """
    Transparent router that delegates to the configured backend.

    Backend selection (AGENTMAX_AI_BACKEND / AGENTMAX_BACKEND env var or config):
      "claude"     -> Anthropic Claude API (cloud)
      "lmstudio"   -> LM Studio local server (offline, private)
      "local_peft" -> in-process PEFT/LoRA adapter
      "llamacpp"   -> llama.cpp llama-server sidecar for GGUF files

    All backends implement the same BaseAIClient interface, so agents work
    identically regardless of which backend is active.
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._backend: BaseAIClient | None = None

    def _build_backend(self) -> BaseAIClient:
        backend_name = self._config.ai.backend.lower()

        if backend_name == "lmstudio":
            from core.ai.lmstudio_client import LMStudioClient

            log.info(
                "ai_router.backend_selected",
                backend="lmstudio",
                url=self._config.ai.base_url_resolved,
            )
            return LMStudioClient(self._config.ai)

        if backend_name == "local_peft":
            # In-process PEFT/LoRA adapter (AgentMax V2.1).
            # This is not GGUF and should not be presented as one.
            from core.ai.local_peft_client import LocalPEFTClient

            log.info(
                "ai_router.backend_selected",
                backend="local_peft",
                base_model=getattr(self._config.ai, "local_peft_base_model", ""),
                adapter=getattr(self._config.ai, "local_peft_adapter_path", ""),
            )
            return LocalPEFTClient(self._config.ai)

        if backend_name == "llamacpp":
            from core.ai.llamacpp_client import LlamaCppSidecarClient

            log.info(
                "ai_router.backend_selected",
                backend="llamacpp",
                url=self._config.ai.base_url_resolved,
                model_path=getattr(self._config.ai, "llama_cpp_model_path", ""),
            )
            return LlamaCppSidecarClient(self._config.ai)

        # Default: Claude
        if not self._config.ai.anthropic_api_key:
            log.warning(
                "ai_router.no_api_key -- ANTHROPIC_API_KEY not set. "
                "Set AGENTMAX_BACKEND=lmstudio, local_peft, or llamacpp to use a local backend."
            )
        from core.ai.claude_client import ClaudeClient

        log.info("ai_router.backend_selected", backend="claude", model=self._config.ai.model)
        return ClaudeClient(self._config.ai)

    @property
    def _client(self) -> BaseAIClient:
        if self._backend is None:
            self._backend = self._build_backend()
        return self._backend

    def _breaker(self) -> CircuitBreaker:
        backend = self._config.ai.backend.lower()
        if backend in {"lmstudio", "local_peft", "llamacpp"}:
            return _local_breaker
        return _cloud_breaker

    @retry_async(max_attempts=3, base_delay=2.0, max_delay=30.0)
    async def vision_query(
        self,
        system: str,
        prompt: str,
        image_b64: str,
        max_tokens: int | None = None,
    ) -> str:
        async with self._breaker():
            return await self._client.vision_query(system, prompt, image_b64, max_tokens)

    @retry_async(max_attempts=3, base_delay=2.0, max_delay=30.0)
    async def text_query(
        self,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
    ) -> str:
        async with self._breaker():
            return await self._client.text_query(system, prompt, max_tokens)

    @property
    def backend_name(self) -> str:
        return self._client.backend_name

    @property
    def model_name(self) -> str:
        return self._client.model_name

    @property
    def supports_vision(self) -> bool:
        return self._client.supports_vision

    @property
    def usage_stats(self) -> dict[str, Any]:
        return self._client.usage_stats

    async def switch_backend(self, backend: str) -> bool:
        """Hot-swap the AI backend without restarting. Returns True on success."""
        old = self._backend
        self._backend = None
        self._config.ai.backend = backend
        try:
            _ = self._client
            log.info("ai_router.switched", backend=backend)
            return True
        except Exception as exc:
            log.error("ai_router.switch_failed", backend=backend, error=str(exc))
            self._backend = old
            return False

    async def health_check(self) -> dict[str, Any]:
        if hasattr(self._client, "health_check"):
            return await self._client.health_check()
        return {"ok": True, "backend": self.backend_name}
