"""
LocalPEFTClient — in-process backend for AgentMax (PEFT/LoRA adapter).

Carga el modelo base (``unsloth/Qwen3-VL-8B-Thinking``) y aplica el adapter
LoRA local (por defecto ``models/AgentMax/V2.1/adapter``). NO carga el
adapter como modelo completo: PEFT requiere base + adapter.

Estados internos
----------------
NOT_LOADED   -- recien construido, todavia no se intento cargar nada
LOADING      -- carga en curso (lock detiene calls concurrentes)
READY        -- base + adapter cargados, generate() disponible
UNAVAILABLE  -- carga fallo (sin GPU / sin deps / archivos faltantes / OOM).
                Toda call siguiente devuelve un mensaje "model_unavailable"
                sin crashear el runtime.

Failure taxonomy
----------------
- ``missing_dependency``      torch / transformers / peft / unsloth no instalados
- ``no_cuda``                 torch.cuda.is_available() == False
- ``adapter_missing_files``   los 6 archivos requeridos no estan
- ``base_model_load_failed``  excepcion al traer el base
- ``adapter_load_failed``     excepcion al hacer load_adapter()
- ``oom``                     CUDA OOM en load o inferencia
- ``unknown``                 cualquier otra cosa

Logs por query
--------------
Cada llamada emite:
- ``local_peft.user_message``     (con preview del prompt)
- ``local_peft.raw_response``     (a nivel DEBUG, contiene <think>)
- ``local_peft.sanitized_response``
- ``local_peft.tool_calls``       (si detecta marcadores)
- ``local_peft.outcome``          (ok | unavailable | error)

Tambien publica al EventBus (cuando observability esta ON):
``ai.thinking`` / ``ai.tokens`` / ``ai.response`` — misma forma que
LMStudioClient para que la UI no distinga el backend.

Reglas
------
- NO carga el adapter como modelo completo.
- NO exporta GGUF.
- NO toca LM Studio.
- NO mergea base+adapter.
- NO borra archivos.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import structlog

from core.ai.base_client import BaseAIClient
from core.ai.response_sanitizer import sanitize_agent_response
from core.ai.thinking_parser import has_thinking, split_thinking

log = structlog.get_logger(__name__)


_REQUIRED_ADAPTER_FILES = (
    "adapter_config.json",
    "adapter_model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "processor_config.json",
    "chat_template.jinja",
)


# ── Errors / sentinels ──────────────────────────────────────────────────────


class ModelUnavailable(Exception):
    """Raised when the model cannot serve a request. NEVER crashes the runtime —
    the router catches this and returns the message string to upstream."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _unavailable_message(reason: str, detail: str = "") -> str:
    """User-visible fallback when AgentMax is unavailable.

    Kept polite + machine-parseable (`[model_unavailable:<reason>]`).
    """
    suffix = f": {detail}" if detail else ""
    return (
        f"[model_unavailable:{reason}] AgentMax V2.1 no puede atender esta "
        f"solicitud ahora{suffix}. Revisa las dependencias (torch+CUDA, peft, "
        "transformers, unsloth) y la ruta del adapter en config.ai.local_peft_adapter_path."
    )


# ── Tool-call detection (best-effort, model-format-aware) ───────────────────
# Qwen-style chat templates emit tool calls either as <tool_call>{...}</tool_call>
# blocks or as a plain JSON dict with a 'tool_calls' key. We extract conservatively.

_TOOL_CALL_RE = re.compile(
    r"<\s*tool_call\s*>(.*?)<\s*/\s*tool_call\s*>", re.IGNORECASE | re.DOTALL
)


def _detect_tool_calls(text: str) -> list[dict]:
    """Return parsed tool calls when present in the response, else []."""
    if not text:
        return []
    out: list[dict] = []
    for m in _TOOL_CALL_RE.finditer(text):
        body = m.group(1).strip()
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                out.append(parsed)
        except Exception:  # noqa: BLE001
            out.append({"raw": body[:240]})
    return out


# ── Client ──────────────────────────────────────────────────────────────────


class LocalPEFTClient(BaseAIClient):
    """In-process AgentMax backend via unsloth.FastVisionModel + PEFT adapter."""

    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    READY = "ready"
    UNAVAILABLE = "unavailable"

    def __init__(self, config: Any) -> None:
        self._config = getattr(config, "lmstudio", config)  # accept both shapes
        # All knobs are read from AIConfig flat fields (with sensible defaults)
        self._base_model: str = getattr(
            config, "local_peft_base_model", "unsloth/Qwen3-VL-8B-Thinking"
        )
        self._adapter_path: Path = (
            Path(getattr(config, "local_peft_adapter_path", ""))
            if hasattr(config, "local_peft_adapter_path")
            else Path("")
        )
        self._load_in_4bit: bool = bool(getattr(config, "local_peft_load_in_4bit", True))
        self._max_new_tokens_default: int = int(getattr(config, "local_peft_max_new_tokens", 512))
        self._temperature: float = float(getattr(config, "temperature", 0.4) or 0.4)
        self._timeout_sec: float = float(getattr(config, "timeout_sec", 300.0) or 300.0)

        # State
        self._state: str = self.NOT_LOADED
        self._failure_reason: str = ""
        self._failure_detail: str = ""
        self._model: Any = None
        self._tokenizer: Any = None
        self._load_lock = asyncio.Lock()

        # Stats
        self._call_count = 0
        self._total_tokens = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._thinking_blocks = 0
        self._thinking_chars = 0

        # Observability (matches LMStudioClient semantics)
        cfg_obs = getattr(config, "expose_thinking", None)
        self._expose_thinking: bool = bool(cfg_obs) if cfg_obs is not None else False
        env_obs = (os.environ.get("AGENTMAX_OBSERVABILITY") or "").strip().lower()
        if env_obs in {"0", "false", "no", "off"}:
            self._observability = False
        elif env_obs in {"1", "true", "yes", "on"}:
            self._observability = True
        else:
            self._observability = True  # default ON

        log.info(
            "local_peft.client_init",
            base_model=self._base_model,
            adapter=str(self._adapter_path),
            load_in_4bit=self._load_in_4bit,
            observability=self._observability,
        )

    # ── Lifecycle / availability checks ─────────────────────────────────────

    def _verify_adapter_files(self) -> tuple[bool, list[str]]:
        """Return (ok, missing). Does NOT raise."""
        if not self._adapter_path.exists() or not self._adapter_path.is_dir():
            return False, list(_REQUIRED_ADAPTER_FILES)
        missing = []
        for fname in _REQUIRED_ADAPTER_FILES:
            fpath = self._adapter_path / fname
            if not fpath.exists() or fpath.stat().st_size == 0:
                missing.append(fname)
        return (not missing), missing

    def _mark_unavailable(self, reason: str, detail: str = "") -> None:
        self._state = self.UNAVAILABLE
        self._failure_reason = reason
        self._failure_detail = detail
        log.error("local_peft.unavailable", reason=reason, detail=detail)

    async def _ensure_loaded(self) -> bool:
        """
        Best-effort lazy load. Returns True if model is READY.

        Never raises — on failure sets state=UNAVAILABLE and returns False.
        """
        if self._state == self.READY:
            return True
        if self._state == self.UNAVAILABLE:
            return False

        async with self._load_lock:
            # Re-check under lock (another coroutine may have raced us)
            if self._state == self.READY:
                return True
            if self._state == self.UNAVAILABLE:
                return False

            self._state = self.LOADING
            log.info(
                "local_peft.loading",
                base_model=self._base_model,
                adapter=str(self._adapter_path),
            )

            # 1) Verify adapter files BEFORE pulling deps (cheap)
            ok, missing = self._verify_adapter_files()
            if not ok:
                self._mark_unavailable(
                    "adapter_missing_files",
                    f"path={self._adapter_path} missing={missing}",
                )
                return False

            # 2) Import deps (heavy; defer)
            try:
                import torch  # noqa: F401
            except Exception as exc:  # noqa: BLE001
                self._mark_unavailable("missing_dependency", f"torch: {exc}")
                return False

            try:
                from peft import PeftModel  # noqa: F401
            except Exception as exc:  # noqa: BLE001
                self._mark_unavailable("missing_dependency", f"peft: {exc}")
                return False

            try:
                from transformers import AutoTokenizer  # noqa: F401
            except Exception as exc:  # noqa: BLE001
                self._mark_unavailable("missing_dependency", f"transformers: {exc}")
                return False

            try:
                pass  # type: ignore[import-not-found]
            except Exception as exc:  # noqa: BLE001
                self._mark_unavailable("missing_dependency", f"unsloth: {exc}")
                return False

            import torch

            if not torch.cuda.is_available():
                self._mark_unavailable(
                    "no_cuda",
                    "torch.cuda.is_available() is False; AgentMax V2.1 needs a CUDA GPU.",
                )
                return False

            # 3) Load base model + adapter, in a worker thread (sync API)
            try:
                model, tokenizer = await asyncio.to_thread(self._load_model_sync)
            except _LoadError as exc:
                self._mark_unavailable(exc.reason, exc.detail)
                return False
            except Exception as exc:  # noqa: BLE001 — catch-all so we never crash runtime
                self._mark_unavailable("unknown", f"{type(exc).__name__}: {exc}")
                return False

            self._model = model
            self._tokenizer = tokenizer
            self._state = self.READY
            log.info("local_peft.ready", base_model=self._base_model)
            return True

    def _load_model_sync(self) -> tuple[Any, Any]:
        """Blocking load — call only from a worker thread via asyncio.to_thread."""
        import torch
        from unsloth import FastVisionModel  # type: ignore[import-not-found]

        try:
            model, tokenizer = FastVisionModel.from_pretrained(
                model_name=self._base_model,
                load_in_4bit=self._load_in_4bit,
            )
        except torch.cuda.OutOfMemoryError as exc:
            raise _LoadError("oom", f"base_model_load: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise _LoadError("base_model_load_failed", f"{type(exc).__name__}: {exc}") from exc

        try:
            model.load_adapter(str(self._adapter_path), adapter_name="AgentMax_v2_1")
            model.set_adapter("AgentMax_v2_1")
        except torch.cuda.OutOfMemoryError as exc:
            raise _LoadError("oom", f"adapter_load: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise _LoadError("adapter_load_failed", f"{type(exc).__name__}: {exc}") from exc

        FastVisionModel.for_inference(model)
        return model, tokenizer

    # ── BaseAIClient API ────────────────────────────────────────────────────

    @property
    def backend_name(self) -> str:
        return "local_peft"

    @property
    def model_name(self) -> str:
        # The "effective" model is base + adapter; report a stable label.
        return f"{self._base_model}+AgentMax_v2_1"

    @property
    def supports_vision(self) -> bool:
        # The base is Qwen3-VL → vision-capable when loaded.
        return True

    @property
    def usage_stats(self) -> dict[str, Any]:
        return {
            "backend": "local_peft",
            "state": self._state,
            "base_model": self._base_model,
            "adapter_path": str(self._adapter_path),
            "ready": self._state == self.READY,
            "failure_reason": self._failure_reason,
            "calls": self._call_count,
            "total_tokens": self._total_tokens,
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "thinking_blocks": self._thinking_blocks,
            "thinking_chars": self._thinking_chars,
        }

    async def text_query(self, system: str, prompt: str, max_tokens: int | None = None) -> str:
        return await self._query(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            user_preview=prompt,
            max_tokens=max_tokens,
        )

    async def vision_query(
        self,
        system: str,
        prompt: str,
        image_b64: str,
        max_tokens: int | None = None,
    ) -> str:
        # We treat the image as a data URL embedded in the user message.
        # The base model is Qwen3-VL; if ever the model isn't VL we surface
        # the same model_unavailable mechanism (handled inside _query).
        return await self._query(
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": f"data:image/png;base64,{image_b64}"},
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
            user_preview=prompt,
            max_tokens=max_tokens,
            has_image=True,
        )

    async def chat_query(
        self,
        system: str,
        history: list[dict],
        *,
        image_b64: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        msgs: list[dict] = [{"role": "system", "content": system}]
        for i, m in enumerate(history):
            if i == len(history) - 1 and m["role"] == "user" and image_b64:
                msgs.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "image": f"data:image/png;base64,{image_b64}"},
                            {"type": "text", "text": m["content"]},
                        ],
                    }
                )
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        return await self._query(
            messages=msgs,
            user_preview=str(last_user)[:240] if isinstance(last_user, str) else "<multimodal>",
            max_tokens=max_tokens,
            has_image=bool(image_b64),
        )

    # ── Core query path ─────────────────────────────────────────────────────

    async def _query(
        self,
        *,
        messages: list[dict],
        user_preview: str,
        max_tokens: int | None = None,
        has_image: bool = False,
    ) -> str:
        self._call_count += 1
        t0 = time.perf_counter()
        log.info(
            "local_peft.user_message",
            preview=str(user_preview)[:200].replace("\n", " "),
            has_image=has_image,
        )

        loaded = await self._ensure_loaded()
        if not loaded:
            text = _unavailable_message(self._failure_reason, self._failure_detail)
            log.warning(
                "local_peft.outcome",
                outcome="unavailable",
                reason=self._failure_reason,
                ms=round((time.perf_counter() - t0) * 1000, 1),
            )
            return text

        # Generate in worker thread — keeps event loop responsive
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self._generate_sync,
                    messages,
                    max_tokens or self._max_new_tokens_default,
                ),
                timeout=self._timeout_sec,
            )
        except TimeoutError:
            log.error(
                "local_peft.outcome",
                outcome="error",
                reason="timeout",
                ms=round((time.perf_counter() - t0) * 1000, 1),
            )
            return _unavailable_message("timeout", f">{self._timeout_sec}s")
        except Exception as exc:  # noqa: BLE001
            log.error(
                "local_peft.outcome",
                outcome="error",
                reason=f"{type(exc).__name__}: {exc}",
                ms=round((time.perf_counter() - t0) * 1000, 1),
            )
            return _unavailable_message("inference_error", str(exc)[:120])

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return await self._postprocess(raw, elapsed_ms=elapsed_ms)

    def _generate_sync(self, messages: list[dict], max_new_tokens: int) -> str:
        """Blocking inference. Returns raw decoded text (only new tokens)."""
        import torch

        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        prompt_len = inputs["input_ids"].shape[1]

        with torch.inference_mode():
            out = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=self._temperature > 0,
                temperature=self._temperature,
                top_p=1.0,
                repetition_penalty=1.05,
            )

        new_tokens = out[0, prompt_len:]
        # Track approximate token counts (best-effort, since usage isn't reported by HF)
        completion_tok = int(new_tokens.shape[0])
        prompt_tok = int(prompt_len)
        self._prompt_tokens += prompt_tok
        self._completion_tokens += completion_tok
        self._total_tokens += prompt_tok + completion_tok
        return self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    async def _postprocess(self, raw: str, *, elapsed_ms: float) -> str:
        """Apply sanitizer + emit per-query logs + publish bus events."""
        # DEBUG-only raw_response so it never surfaces to UI by accident.
        log.debug(
            "local_peft.raw_response",
            length=len(raw),
            preview=raw[:400].replace("\n", " "),
        )

        # Sanitize: strip <think>, leak guards, hard cap
        had_thinking = has_thinking(raw)
        thinking_text = ""
        if had_thinking:
            thinking_text, _ = split_thinking(raw)
            self._thinking_blocks += 1
            self._thinking_chars += len(thinking_text)
            log.info(
                "local_peft.thinking",
                chars=len(thinking_text),
                preview=thinking_text[:200].replace("\n", " ")
                + ("..." if len(thinking_text) > 200 else ""),
            )
        sanitized = sanitize_agent_response(raw)

        tool_calls = _detect_tool_calls(raw)
        if tool_calls:
            log.info(
                "local_peft.tool_calls",
                count=len(tool_calls),
                first=str(tool_calls[0])[:240],
            )

        log.info(
            "local_peft.sanitized_response",
            chars=len(sanitized.visible_text),
            filtered_thinking=sanitized.filtered_thinking,
            removed_system_prompt=sanitized.removed_system_prompt,
            truncated=sanitized.truncated,
        )

        log.info(
            "local_peft.outcome",
            outcome="ok",
            ms=round(elapsed_ms, 1),
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._total_tokens,
        )

        # Bus events — mirror LMStudioClient's shape so UI doesn't branch
        if self._observability:
            await self._emit_events(
                thinking_text=thinking_text,
                had_thinking=had_thinking,
                clean_chars=len(sanitized.visible_text),
                tool_calls=tool_calls,
                elapsed_ms=elapsed_ms,
            )

        return raw if self._expose_thinking else sanitized.visible_text

    async def _emit_events(
        self,
        *,
        thinking_text: str,
        had_thinking: bool,
        clean_chars: int,
        tool_calls: list[dict],
        elapsed_ms: float,
    ) -> None:
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
                        source="local_peft",
                        priority=PRIORITY_LOW,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("local_peft.event_publish_failed", topic=topic, error=str(exc))

        if had_thinking and thinking_text:
            await _safe_publish(
                "ai.thinking",
                {
                    "backend": "local_peft",
                    "model": self.model_name,
                    "thinking": thinking_text,
                    "chars": len(thinking_text),
                },
            )

        await _safe_publish(
            "ai.tokens",
            {
                "backend": "local_peft",
                "model": self.model_name,
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "total_tokens": self._total_tokens,
                "cumulative_total": self._total_tokens,
            },
        )

        await _safe_publish(
            "ai.response",
            {
                "backend": "local_peft",
                "model": self.model_name,
                "ms": round(elapsed_ms, 1),
                "chars": clean_chars,
                "had_thinking": had_thinking,
                "tool_calls": len(tool_calls),
            },
        )

    # ── Health check ────────────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        ok, missing = self._verify_adapter_files()
        return {
            "ok": self._state in (self.NOT_LOADED, self.READY),  # not_loaded is not unhealthy
            "backend": "local_peft",
            "state": self._state,
            "adapter_files_ok": ok,
            "missing_adapter_files": missing,
            "failure_reason": self._failure_reason,
            "failure_detail": self._failure_detail,
        }


# ── Internal ────────────────────────────────────────────────────────────────


class _LoadError(Exception):
    """Internal load failure carrying the reason taxonomy."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


__all__ = ["LocalPEFTClient", "ModelUnavailable"]
