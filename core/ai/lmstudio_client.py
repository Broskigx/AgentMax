"""LM Studio client -- Native API (http://localhost:1234/api/v1).

AgentMax (Qwen3-VL-Thinking) corre dentro de LM Studio. Cuando la flag
``AGENTMAX_OBSERVABILITY=1`` esta activa, este cliente:
  - extrae los bloques <think>...</think> ANTES de devolver el texto
  - publica eventos al EventBus:
      ai.thinking      -> con el razonamiento interno (no se muestra al user)
      ai.tokens        -> uso de tokens del request
      ai.response      -> meta del request (ms, chars, had_thinking)
  - loguea ambos a structlog para que aparezcan en la consola del runtime

Cuando la flag esta APAGADA (default), el cliente se comporta como antes
del patch: devuelve el texto crudo (incluyendo <think> si lo hay), no
publica eventos, y solo cuenta `total_tokens` a nivel debug.

La flag se puede prender via env var o pasandole ``observability=True`` al
config del cliente.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import structlog

from core.ai.base_client import BaseAIClient
from core.ai.thinking_parser import has_thinking, split_thinking

log = structlog.get_logger(__name__)


_OBSERVABILITY_ENV = "AGENTMAX_OBSERVABILITY"


def _observability_enabled_default() -> bool:
    """
    Read the env flag. Default: **ON**. Set AGENTMAX_OBSERVABILITY=0
    to disable (fall back to legacy behaviour).
    """
    val = (os.environ.get(_OBSERVABILITY_ENV) or "").strip().lower()
    if val in {"0", "false", "no", "off"}:
        return False
    return True


def _extract_content(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data or "")

    for key in ("content", "reply", "text"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value

    message = data.get("message")
    if isinstance(message, dict):
        content = _normalize_content(message.get("content"))
        if content:
            return content

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = _normalize_content(message.get("content"))
                if content:
                    return content
            content = _normalize_content(first.get("text"))
            if content:
                return content

    return ""


def _normalize_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return ""


class LMStudioClient(BaseAIClient):
    """
    Client for LM Studio 0.4.0+ Native REST API.

    Native endpoints:
      GET  /api/v1/models  -- list loaded models
      POST /api/v1/chat    -- chat (text + vision)
    """

    def __init__(self, config: Any) -> None:
        # Accept both the current flat AIConfig and older nested config shapes.
        # This keeps runtime routing compatible with existing callers while
        # avoiding the config.ai.lmstudio AttributeError that blocked planning.
        self._config = getattr(config, "lmstudio", config)
        # Resolve base URL: check api_base_url first, then legacy base_url, then fallback to host:port
        base_url = getattr(self._config, "api_base_url", None) or getattr(
            self._config, "base_url", None
        )
        if not base_url:
            host = getattr(self._config, "lmstudio_host", "127.0.0.1")
            port = getattr(self._config, "lmstudio_port", 1234)
            base_url = f"http://{host}:{port}/v1"
        self._base_url = str(base_url).rstrip("/")
        self._active_model: str = ""
        self._vision_model: str = ""
        self._call_count = 0
        self._total_tokens = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._thinking_chars = 0
        self._thinking_blocks = 0
        # Default: AgentMax's <think> is INTERNAL. We strip it before
        # returning text to upstream agents. Override with
        # config.expose_thinking=True to keep raw text intact.
        self._expose_thinking: bool = bool(getattr(self._config, "expose_thinking", False))
        # Master switch for the new observability layer. OFF by default
        # until the full AgentMax flow is wired (UI subscriptions, etc.).
        cfg_observability = getattr(self._config, "observability", None)
        if cfg_observability is None:
            self._observability: bool = _observability_enabled_default()
        else:
            self._observability = bool(cfg_observability)
        # Per-model "no vision" cache. Some local GGUFs advertise themselves
        # as VL but reject image payloads at inference time. When we see the
        # backend say so once, we stop sending image_url for the rest of the
        # session and degrade to text-only -- much faster than waiting for
        # the router to spend its 3 retries + open the breaker every time.
        self._models_without_vision: set[str] = set()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._config.timeout_sec),
            base_url=self._base_url,
        )
        log.info(
            "lmstudio.native_client_init",
            forced_url=self._base_url,
            expose_thinking=self._expose_thinking,
            observability=self._observability,
        )

    async def _get_active_model(self) -> str:
        if self._active_model:
            return self._active_model
        try:
            resp = await self._client.get("/models")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", []) or data.get("models", [])

            if not models:
                raise RuntimeError("No models loaded in LM Studio. Load a model first.")

            preferred = self._config.model
            if preferred:
                for m in models:
                    m_id = m.get("id") or m.get("path") or ""
                    if preferred.lower() in m_id.lower():
                        self._active_model = m_id
                        break
            if not self._active_model:
                self._active_model = models[0].get("id") or models[0].get("path")

            log.info("lmstudio.model_selected", model=self._active_model)
            return self._active_model
        except Exception as exc:
            log.error("lmstudio.model_discovery_failed", error=str(exc))
            raise ConnectionError(
                f"Cannot connect to LM Studio at {self._base_url}. Check if it's running."
            ) from exc

    async def _get_vision_model(self) -> str:
        if self._vision_model:
            return self._vision_model
        preferred = self._config.vision_model
        if preferred:
            self._vision_model = preferred
            return self._vision_model
        self._vision_model = await self._get_active_model()
        return self._vision_model

    async def vision_query(
        self, system: str, prompt: str, image_b64: str, max_tokens: int | None = None
    ) -> str:
        model = await self._get_vision_model()
        # Cached fast-path: this model already told us it can't do vision —
        # don't waste a round-trip + 400.
        if model in self._models_without_vision:
            log.info("lmstudio.vision_skipped_text_only", model=model)
            return await self.text_query(system, prompt, max_tokens)

        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        try:
            return await self._chat(model, messages, max_tokens)
        except Exception as exc:  # noqa: BLE001
            # Two real-world failure modes that should degrade to text-only
            # WITHOUT burning the router's retry quota or opening the breaker:
            #   1. Backend has no vision encoder (rejects image_url payloads).
            #   2. Backend has a tight context window and the screenshot tokens
            #      push the prompt over the limit (the model is fine, just
            #      under-configured).
            err = str(exc).lower()
            is_400 = "400" in err
            vision_signal = any(k in err for k in ("image", "audio", "multimodal", "unsupported"))
            context_signal = any(
                k in err
                for k in (
                    "context size",
                    "context length",
                    "context window",
                    "exceeds the available",
                    "max_position_embed",
                )
            )
            should_degrade = is_400 and (vision_signal or context_signal)
            if not should_degrade:
                raise
            reason = "context_overflow" if context_signal else "vision_unsupported"
            log.warning(
                "lmstudio.vision_degraded_to_text_only",
                model=model,
                reason=reason,
                error=str(exc)[:240],
            )
            # Cache so subsequent calls skip the image upload entirely.
            self._models_without_vision.add(model)
            # Single, immediate, in-process text-only retry. This bypasses the
            # router's retry counter and the circuit breaker because we never
            # raise — the upstream agent sees a normal text response.
            return await self.text_query(system, prompt, max_tokens)

    async def text_query(self, system: str, prompt: str, max_tokens: int | None = None) -> str:
        model = await self._get_active_model()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        return await self._chat(model, messages, max_tokens)

    async def chat_query(
        self,
        system: str,
        history: list[dict],
        *,
        image_b64: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        model = await (self._get_vision_model() if image_b64 else self._get_active_model())
        messages: list[dict] = [{"role": "system", "content": system}]
        for i, msg in enumerate(history):
            if i == len(history) - 1 and msg["role"] == "user" and image_b64:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                            },
                            {"type": "text", "text": msg["content"]},
                        ],
                    }
                )
            else:
                messages.append({"role": msg["role"], "content": msg["content"]})
        return await self._chat(model, messages, max_tokens)

    async def _chat(self, model: str, messages: list[dict], max_tokens: int | None = None) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens or self._config.max_tokens,
            "temperature": self._config.temperature,
            "stream": False,
        }
        t0 = time.perf_counter()
        try:
            log.info("lmstudio.request", url=f"{self._base_url}/chat/completions", model=model)
            resp = await self._client.post("/chat/completions", json=payload)
            if resp.status_code >= 400:
                # Surface the response body in the exception. httpx.HTTPStatusError
                # default str() drops it, which made caller-side detection of
                # "model lacks vision encoder" impossible.
                body = (resp.text or "")[:480]
                raise RuntimeError(f"lmstudio HTTP {resp.status_code}: {body}")
            data = resp.json()
            elapsed_ms = (time.perf_counter() - t0) * 1000

            raw_content = _extract_content(data)
            if not raw_content:
                raise RuntimeError("LM Studio responded, but no assistant text could be extracted.")

            # ── Tokens (siempre se cuentan, aunque no se loguen) ─────────────
            usage = data.get("usage") or {}
            prompt_tok = int(usage.get("prompt_tokens", 0) or 0)
            completion_tok = int(usage.get("completion_tokens", 0) or 0)
            total_tok = int(usage.get("total_tokens", 0) or 0) or (prompt_tok + completion_tok)

            self._total_tokens += total_tok
            self._prompt_tokens += prompt_tok
            self._completion_tokens += completion_tok
            self._call_count += 1

            # ── Modo legacy (observability OFF) ──────────────────────────────
            # Comportamiento previo al patch: devolver texto crudo, log debug.
            if not self._observability:
                log.debug("lmstudio.response", ms=f"{elapsed_ms:.0f}", tokens=total_tok)
                return raw_content

            # ── Modo observability (ON): thinking-strip + logs + eventos ─────
            had_thinking = has_thinking(raw_content)
            thinking_text, clean_content = (
                split_thinking(raw_content) if had_thinking else ("", raw_content)
            )

            if had_thinking:
                self._thinking_blocks += 1
                self._thinking_chars += len(thinking_text)
                log.info(
                    "AgentMax.thinking",
                    model=model,
                    chars=len(thinking_text),
                    preview=thinking_text[:200].replace("\n", " ")
                    + ("..." if len(thinking_text) > 200 else ""),
                )

            log.info(
                "AgentMax.tokens",
                model=model,
                prompt=prompt_tok,
                completion=completion_tok,
                total=total_tok,
                ms=f"{elapsed_ms:.0f}",
                cumulative_total=self._total_tokens,
            )

            await self._emit_events(
                model=model,
                thinking_text=thinking_text,
                clean_chars=len(clean_content),
                had_thinking=had_thinking,
                prompt_tok=prompt_tok,
                completion_tok=completion_tok,
                total_tok=total_tok,
                elapsed_ms=elapsed_ms,
            )

            return raw_content if self._expose_thinking else clean_content
        except Exception as exc:
            log.error("lmstudio.chat_failed", error=str(exc))
            raise

    async def _emit_events(
        self,
        *,
        model: str,
        thinking_text: str,
        clean_chars: int,
        had_thinking: bool,
        prompt_tok: int,
        completion_tok: int,
        total_tok: int,
        elapsed_ms: float,
    ) -> None:
        """Publish thinking/tokens/response events. Never raises."""
        try:
            from core.event_bus import PRIORITY_LOW, Event, get_bus

            bus = get_bus()
        except Exception:  # noqa: BLE001
            return

        async def _safe_publish(topic: str, payload: dict) -> None:
            try:
                await bus.publish(
                    Event(
                        topic=topic,
                        payload=payload,
                        source="lmstudio",
                        priority=PRIORITY_LOW,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("lmstudio.event_publish_failed", topic=topic, error=str(exc))

        if had_thinking and thinking_text:
            await _safe_publish(
                "ai.thinking",
                {
                    "backend": "lmstudio",
                    "model": model,
                    "thinking": thinking_text,
                    "chars": len(thinking_text),
                },
            )

        await _safe_publish(
            "ai.tokens",
            {
                "backend": "lmstudio",
                "model": model,
                "prompt_tokens": prompt_tok,
                "completion_tokens": completion_tok,
                "total_tokens": total_tok,
                "cumulative_total": self._total_tokens,
            },
        )

        await _safe_publish(
            "ai.response",
            {
                "backend": "lmstudio",
                "model": model,
                "ms": round(elapsed_ms, 1),
                "chars": clean_chars,
                "had_thinking": had_thinking,
            },
        )

    async def health_check(self) -> dict[str, Any]:
        try:
            resp = await self._client.get("/models")
            data = resp.json()
            models = data.get("data", []) or data.get("models", [])
            return {
                "ok": True,
                "url": self._base_url,
                "models": [m.get("id") or m.get("path") for m in models],
            }
        except Exception as exc:
            return {"ok": False, "url": self._base_url, "error": str(exc)}

    @property
    def backend_name(self) -> str:
        return "lmstudio"

    @property
    def model_name(self) -> str:
        return self._active_model or "auto"

    @property
    def usage_stats(self) -> dict[str, Any]:
        return {
            "backend": "lmstudio",
            "model": self._active_model or "auto",
            "calls": self._call_count,
            "total_tokens": self._total_tokens,
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "thinking_blocks": self._thinking_blocks,
            "thinking_chars": self._thinking_chars,
            "expose_thinking": self._expose_thinking,
        }

    async def close(self) -> None:
        await self._client.aclose()
