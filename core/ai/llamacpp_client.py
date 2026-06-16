"""llama.cpp GGUF sidecar client for AgentMax.

This backend talks to llama.cpp's OpenAI-compatible ``llama-server``. It is a
true GGUF path and intentionally stays separate from the PEFT/LoRA adapter.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

from core.ai.base_client import BaseAIClient
from core.ai.thinking_parser import has_thinking, split_thinking

log = structlog.get_logger(__name__)


def _extract_content(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data or "")
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content.strip()
            text = first.get("text")
            if isinstance(text, str):
                return text.strip()
    content = data.get("content") or data.get("reply") or data.get("text")
    return content.strip() if isinstance(content, str) else ""


class LlamaCppSidecarClient(BaseAIClient):
    """Client for llama.cpp ``llama-server`` with optional sidecar launch."""

    def __init__(self, config: Any) -> None:
        self._config = getattr(config, "llamacpp", config)
        self._host = getattr(self._config, "llama_cpp_host", "127.0.0.1")
        self._port = int(getattr(self._config, "llama_cpp_port", 8080))
        self._base_url = (
            getattr(self._config, "api_base_url", None)
            or f"http://{self._host}:{self._port}/v1"
        ).rstrip("/")
        self._model_path = str(getattr(self._config, "llama_cpp_model_path", "") or "")
        self._server_bin = str(getattr(self._config, "llama_cpp_server_bin", "llama-server"))
        self._context_size = int(getattr(self._config, "llama_cpp_context_size", 4096))
        self._threads = int(getattr(self._config, "llama_cpp_threads", 0))
        self._gpu_layers = int(getattr(self._config, "llama_cpp_gpu_layers", 0))
        self._mmproj_path = str(getattr(self._config, "llama_cpp_mmproj_path", "") or "")
        self._active_model = str(getattr(self._config, "model", "") or "")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(getattr(self._config, "timeout_sec", 300.0))),
            base_url=self._base_url,
        )
        self._process: asyncio.subprocess.Process | None = None
        self._call_count = 0
        self._total_tokens = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._thinking_blocks = 0
        self._thinking_chars = 0
        log.info(
            "llamacpp.client_init",
            base_url=self._base_url,
            model_path=self._model_path,
            server_bin=self._server_bin,
        )

    async def _request_models(self) -> list[str]:
        response = await self._client.get("/models")
        response.raise_for_status()
        data = response.json()
        raw_models = data.get("data") or data.get("models") or []
        models: list[str] = []
        for item in raw_models:
            if isinstance(item, dict):
                model_id = item.get("id") or item.get("path")
                if model_id:
                    models.append(str(model_id))
            elif isinstance(item, str):
                models.append(item)
        return models

    def _resolve_server_bin(self) -> str | None:
        candidate = Path(self._server_bin)
        if candidate.exists():
            return str(candidate)
        return shutil.which(self._server_bin)

    def _validate_launch_config(self) -> str | None:
        if not self._model_path.strip():
            return "AGENTMAX_GGUF_MODEL_PATH is not set."
        if not Path(self._model_path).exists():
            return f"GGUF model path does not exist: {self._model_path}"
        if not self._resolve_server_bin():
            return f"llama-server binary not found: {self._server_bin}"
        if self._mmproj_path and not Path(self._mmproj_path).exists():
            return f"llama.cpp mmproj file does not exist: {self._mmproj_path}"
        return None

    async def _start_sidecar(self) -> None:
        if self._process and self._process.returncode is None:
            return

        error = self._validate_launch_config()
        if error:
            raise RuntimeError(error)

        server_bin = self._resolve_server_bin()
        if not server_bin:
            raise RuntimeError(f"llama-server binary not found: {self._server_bin}")

        args = [
            server_bin,
            "--model",
            self._model_path,
            "--host",
            self._host,
            "--port",
            str(self._port),
            "--ctx-size",
            str(self._context_size),
        ]
        if self._threads > 0:
            args.extend(["--threads", str(self._threads)])
        if self._gpu_layers != 0:
            args.extend(["--n-gpu-layers", str(self._gpu_layers)])
        if self._mmproj_path:
            args.extend(["--mmproj", self._mmproj_path])

        log.info("llamacpp.sidecar_starting", args=args[:1] + ["..."])
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        asyncio.create_task(self._pipe_log(self._process.stdout, "stdout"))
        asyncio.create_task(self._pipe_log(self._process.stderr, "stderr"))

    async def _pipe_log(
        self,
        stream: asyncio.StreamReader | None,
        source: str,
    ) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                log.info("llamacpp.sidecar_log", source=source, line=text)

    async def _ensure_ready(self) -> list[str]:
        try:
            return await self._request_models()
        except Exception:
            pass

        await self._start_sidecar()
        for _ in range(40):
            if self._process and self._process.returncode is not None:
                raise RuntimeError(f"llama-server exited with code {self._process.returncode}")
            try:
                return await self._request_models()
            except Exception:
                await asyncio.sleep(0.25)
        raise RuntimeError(f"llama-server did not become ready at {self._base_url}")

    async def _get_active_model(self) -> str:
        models = await self._ensure_ready()
        if self._active_model:
            for model in models:
                if self._active_model.lower() in model.lower():
                    self._active_model = model
                    return model
        if models:
            self._active_model = models[0]
            return self._active_model
        if self._model_path:
            self._active_model = Path(self._model_path).name
            return self._active_model
        raise RuntimeError("llama-server is ready but returned no models.")

    async def _chat(self, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str:
        model = await self._get_active_model()
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens or int(getattr(self._config, "max_tokens", 4096)),
            "temperature": float(getattr(self._config, "temperature", 0.4)),
            "stream": False,
        }
        t0 = time.perf_counter()
        try:
            response = await self._client.post("/chat/completions", json=payload)
            if response.status_code >= 400:
                body = response.text[:480]
                raise RuntimeError(f"llamacpp HTTP {response.status_code}: {body}")
            data = response.json()
            content = _extract_content(data)
            if not content:
                raise RuntimeError("llama.cpp responded, but no assistant text could be extracted.")

            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
            total_tokens = int(usage.get("total_tokens", 0) or 0) or (
                prompt_tokens + completion_tokens
            )
            self._prompt_tokens += prompt_tokens
            self._completion_tokens += completion_tokens
            self._total_tokens += total_tokens
            self._call_count += 1

            had_thinking = has_thinking(content)
            thinking_text, clean_content = (
                split_thinking(content) if had_thinking else ("", content)
            )
            if had_thinking:
                self._thinking_blocks += 1
                self._thinking_chars += len(thinking_text)

            log.info(
                "llamacpp.response",
                model=model,
                ms=round((time.perf_counter() - t0) * 1000, 1),
                tokens=total_tokens,
                had_thinking=had_thinking,
            )
            return clean_content
        except Exception as exc:
            log.error("llamacpp.chat_failed", error=str(exc))
            raise

    async def text_query(self, system: str, prompt: str, max_tokens: int | None = None) -> str:
        return await self._chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens,
        )

    async def vision_query(
        self,
        system: str,
        prompt: str,
        image_b64: str,
        max_tokens: int | None = None,
    ) -> str:
        if not self.supports_vision:
            log.warning("llamacpp.vision_degraded_to_text", reason="mmproj_not_configured")
            return await self.text_query(system, prompt, max_tokens)
        return await self._chat(
            [
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
            ],
            max_tokens,
        )

    async def chat_query(
        self,
        system: str,
        history: list[dict],
        *,
        image_b64: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for index, message in enumerate(history):
            if index == len(history) - 1 and image_b64 and message.get("role") == "user":
                if not self.supports_vision:
                    messages.append({"role": "user", "content": message.get("content", "")})
                else:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{image_b64}"
                                    },
                                },
                                {"type": "text", "text": message.get("content", "")},
                            ],
                        }
                    )
            else:
                messages.append(
                    {
                        "role": message.get("role", "user"),
                        "content": message.get("content", ""),
                    }
                )
        return await self._chat(messages, max_tokens)

    async def health_check(self) -> dict[str, Any]:
        launch_error = self._validate_launch_config()
        try:
            models = await self._request_models()
            return {
                "ok": True,
                "backend": "llamacpp",
                "url": self._base_url,
                "models": models,
                "model_path": self._model_path,
                "sidecar_pid": self._process.pid if self._process else None,
                "launch_warning": launch_error,
            }
        except Exception as exc:
            return {
                "ok": False,
                "backend": "llamacpp",
                "url": self._base_url,
                "model_path": self._model_path,
                "server_bin": self._server_bin,
                "error": launch_error or str(exc),
            }

    @property
    def backend_name(self) -> str:
        return "llamacpp"

    @property
    def model_name(self) -> str:
        return self._active_model or Path(self._model_path).name or "gguf"

    @property
    def supports_vision(self) -> bool:
        return bool(self._mmproj_path and Path(self._mmproj_path).exists())

    @property
    def usage_stats(self) -> dict[str, Any]:
        return {
            "backend": "llamacpp",
            "model": self.model_name,
            "calls": self._call_count,
            "total_tokens": self._total_tokens,
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "thinking_blocks": self._thinking_blocks,
            "thinking_chars": self._thinking_chars,
            "base_url": self._base_url,
            "model_path": self._model_path,
        }

    async def close(self) -> None:
        await self._client.aclose()
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
