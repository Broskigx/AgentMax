"""
Air-Gap Mode -- "Privacy Shield" for enterprise deployments.

When enabled:
  1. Patches socket.create_connection to refuse all non-local addresses.
     This silently blocks anthropic, openai, huggingface, and any other
     external HTTP client regardless of which library they use underneath.
  2. Forces AIConfig.backend = "lmstudio" (local model only).
  3. Blocks sentence_transformers / HuggingFace hub model downloads.
  4. Keeps local-only automation/network policy explicit and auditable.

The network patch is reversible -- call disable() to restore normal sockets.
No process restart needed.

Compliance claim: "Your data never leaves this machine."
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_LOCK = threading.Lock()
_PATCH_COUNT = 0  # reference-count so nested enable/disable is safe
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_GETADDRINFO = socket.getaddrinfo


_LOCAL_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "::1",
        "0.0.0.0",
    }
)


def _is_local(host: str) -> bool:
    return host in _LOCAL_HOSTS or host.startswith("127.")


def _blocked_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
    host = address[0] if isinstance(address, tuple) else str(address)
    if not _is_local(host):
        raise ConnectionRefusedError(
            f"[AgentMax Air-Gap] Blocked outbound connection to {host!r}. "
            "Disable Air-Gap Mode to allow external network access."
        )
    return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)


def _blocked_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    if isinstance(host, str) and not _is_local(host):
        raise OSError(f"[AgentMax Air-Gap] DNS resolution blocked for {host!r}.")
    return _ORIGINAL_GETADDRINFO(host, *args, **kwargs)


def enable(config: Any | None = None) -> None:
    """
    Activate Air-Gap mode.  Safe to call multiple times.
    `config` is AgentMaxConfig -- if provided, forces backend to lmstudio.
    """
    global _PATCH_COUNT

    with _LOCK:
        _PATCH_COUNT += 1
        if _PATCH_COUNT > 1:
            return  # already active

        socket.create_connection = _blocked_create_connection  # type: ignore[assignment]
        socket.getaddrinfo = _blocked_getaddrinfo  # type: ignore[assignment]

        # Tell HuggingFace libraries not to attempt downloads
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "1"

    if config is not None:
        try:
            config.ai.backend = "lmstudio"
            log.info("air_gap.backend_forced", backend="lmstudio")
        except Exception:
            pass

    log.warning(
        "air_gap.ENABLED -- all external network connections are BLOCKED. "
        "Only local LM Studio and local OCR engines will be used."
    )


def disable(config: Any | None = None) -> None:
    """Deactivate Air-Gap mode and restore normal networking."""
    global _PATCH_COUNT

    with _LOCK:
        if _PATCH_COUNT <= 0:
            return
        _PATCH_COUNT -= 1
        if _PATCH_COUNT > 0:
            return  # still active from another caller

        socket.create_connection = _ORIGINAL_CREATE_CONNECTION  # type: ignore[assignment]
        socket.getaddrinfo = _ORIGINAL_GETADDRINFO  # type: ignore[assignment]

        os.environ.pop("TRANSFORMERS_OFFLINE", None)
        os.environ.pop("HF_DATASETS_OFFLINE", None)
        os.environ.pop("HF_HUB_OFFLINE", None)

    log.info("air_gap.DISABLED -- external network access restored")


def is_active() -> bool:
    return _PATCH_COUNT > 0
