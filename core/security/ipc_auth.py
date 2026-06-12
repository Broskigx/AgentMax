"""
IPC authentication layer for AgentMax — Phase 2.

Goal
----
Stop unauthenticated localhost callers from posting tasks, reading the event
bus, or shutting down the runtime. The fix is a simple bearer token shared
between the Tauri shell (or whoever launches the runtime) and the Python IPC.

Design
------
- One random 32-byte token is generated per runtime boot (base64-urlsafe ~43
  chars). The token is the secret; never derived from anything predictable.
- Token is exposed via:
  - Environment variable ``AGENTMAX_IPC_TOKEN`` (set by the runtime if not
    already present in env). Tauri reads the env var to copy the token into
    its own request headers.
  - File ``%LOCALAPPDATA%/AgentMax/ipc_token`` (or ``~/.config/AgentMax/...``
    on Linux/macOS) with 0600 perms. Same purpose, useful when Tauri can't
    inherit env.
- REST: clients send ``X-AgentMax-Token: <token>``.
- WebSocket: clients send ``{"cmd": "auth", "token": "..."}`` as the first
  message within 5 seconds, or the socket is closed.
- Exemptions: ``/health`` is always allowed (probes, load balancer, dev).
- Constant-time comparison (``hmac.compare_digest``).

Feature flag
------------
Gated behind ``AGENTMAX_IPC_AUTH``. **Default = ON** for the desktop product.
Set ``AGENTMAX_IPC_AUTH=0`` only for isolated compatibility diagnostics.

Rollback
--------
Set ``AGENTMAX_IPC_AUTH=0``. The IPC server falls back to no-auth behaviour.
No code change required.

What this does NOT do
---------------------
- Does NOT protect against a hostile local user with read access to the env
  or the token file. Same trust boundary as the user account.
- Does NOT provide rate limiting (separate concern).
- Does NOT encrypt the channel (loopback only, mTLS would be overkill).
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────
TOKEN_ENV_VAR = "AGENTMAX_IPC_TOKEN"  # noqa: S105 — env-var name, not a secret value
AUTH_FLAG_ENV = "AGENTMAX_IPC_AUTH"
AUTH_HEADER = "X-AgentMax-Token"
WS_HANDSHAKE_TIMEOUT_SEC = 5.0

# Routes that are ALWAYS allowed (no auth check). Keep this list small.
EXEMPT_PATH_PREFIXES: tuple[str, ...] = ("/health", "/api/health")


# ── Token storage location ───────────────────────────────────────────────────


def _agentmax_config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "AgentMax"


def token_file_path() -> Path:
    """OS-appropriate path where the runtime persists the token for Tauri."""
    return _agentmax_config_dir() / "ipc_token"


def legacy_token_file_path() -> Path:
    """Legacy Tauri token file (UUID in ipc.key). Migrated into ipc_token."""
    return _agentmax_config_dir() / "ipc.key"


# ── Token generation / persistence ───────────────────────────────────────────


def _generate_token() -> str:
    """32 random bytes -> base64-urlsafe (~43 chars)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def _write_token_file(token: str) -> Path | None:
    """Persist the token to disk so the Tauri side can read it. Best-effort."""
    path = token_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        # Tighten perms on POSIX. Windows ACLs are inherited from %LOCALAPPDATA%.
        if sys.platform != "win32":
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return path
    except Exception as exc:  # noqa: BLE001
        log.warning("ipc_auth.token_file_write_failed", path=str(path), error=str(exc))
        return None


def ensure_token() -> str:
    """
    Return the current IPC token, generating + persisting one if needed.

    Priority:
      1. If ``AGENTMAX_IPC_TOKEN`` is already in env, use it as-is.
      2. Else if token file exists and is non-empty, load it.
      3. Else generate a new one, export it into the env, and write the file.
    """
    env_token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if env_token:
        return env_token

    path = token_file_path()
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                os.environ[TOKEN_ENV_VAR] = existing
                return existing
        except Exception:  # noqa: BLE001
            pass

    legacy = legacy_token_file_path()
    if legacy.exists():
        try:
            migrated = legacy.read_text(encoding="utf-8").strip()
            if migrated:
                os.environ[TOKEN_ENV_VAR] = migrated
                _write_token_file(migrated)
                return migrated
        except Exception:  # noqa: BLE001
            pass

    token = _generate_token()
    os.environ[TOKEN_ENV_VAR] = token
    _write_token_file(token)
    return token


# ── Auth-enable flag ─────────────────────────────────────────────────────────


def is_auth_enabled(config: Any = None) -> bool:
    """
    Read the env flag first (overrides config). Default: **ON** through config.

    Truthy values: 1, true, yes, on (case-insensitive).
    Anything else (including unset) falls back to the config default.
    """
    raw = (os.environ.get(AUTH_FLAG_ENV) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    # Fall back to config
    return bool(getattr(config, "ipc_auth_enabled", False))


# ── Validation primitives ────────────────────────────────────────────────────


def is_exempt_path(path: str) -> bool:
    """Routes that NEVER require auth, even when the flag is on."""
    p = (path or "").rstrip("/")
    for pref in EXEMPT_PATH_PREFIXES:
        if p == pref or p.startswith(pref + "/"):
            return True
    return False


def validate_token(presented: str | None, expected: str) -> bool:
    """Constant-time comparison. Empty/None presented -> False."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.strip(), expected.strip())


# ── REST middleware (FastAPI-friendly) ───────────────────────────────────────


class IPCAuthError(Exception):
    """Raised when a request fails auth. Caller maps to HTTP 401."""

    def __init__(self, reason: str = "unauthorized"):
        super().__init__(reason)
        self.reason = reason


def check_rest_request(
    *, path: str, headers: dict[str, str], enabled: bool, expected_token: str
) -> None:
    """
    Synchronous check. Raises ``IPCAuthError`` when the request must be rejected.

    Header lookup is case-insensitive (HTTP/2 normalizes to lowercase).
    """
    if not enabled:
        return
    if is_exempt_path(path):
        return
    # FastAPI exposes headers as a Headers obj; accept any dict-like with .get
    presented = headers.get(AUTH_HEADER) or headers.get(AUTH_HEADER.lower())
    if not validate_token(presented, expected_token):
        raise IPCAuthError("missing_or_invalid_token")


# ── WebSocket handshake ──────────────────────────────────────────────────────


async def authenticate_ws(ws: Any, *, expected_token: str, enabled: bool) -> bool:
    """
    First-message handshake. Returns True if the socket is allowed to proceed.

    Protocol when enabled:
      Client -> ``{"cmd": "auth", "token": "<token>"}``
      Server -> ``{"type": "auth_ok"}`` on success
      Server -> ``{"type": "auth_failed", "reason": "..."}`` + close on failure

    When disabled: returns True immediately, no message exchanged.
    """
    if not enabled:
        return True

    try:
        msg = await asyncio.wait_for(ws.recv(), timeout=WS_HANDSHAKE_TIMEOUT_SEC)
    except TimeoutError:
        await _ws_send_safe(ws, {"type": "auth_failed", "reason": "handshake_timeout"})
        await _ws_close_safe(ws, code=4401)
        return False

    payload: dict[str, Any]
    try:
        if isinstance(msg, (bytes, bytearray)):
            # Allow either msgpack-packed or JSON-bytes handshake
            try:
                import msgpack

                payload = msgpack.unpackb(bytes(msg), raw=False)
            except Exception:  # noqa: BLE001
                payload = json.loads(msg.decode("utf-8", "replace"))
        else:
            payload = json.loads(msg)
        if not isinstance(payload, dict):
            raise ValueError("payload is not a dict")
    except Exception as exc:  # noqa: BLE001
        await _ws_send_safe(ws, {"type": "auth_failed", "reason": f"bad_payload:{exc}"})
        await _ws_close_safe(ws, code=4400)
        return False

    if payload.get("cmd") != "auth":
        await _ws_send_safe(ws, {"type": "auth_failed", "reason": "expected_auth_cmd"})
        await _ws_close_safe(ws, code=4401)
        return False

    if not validate_token(payload.get("token"), expected_token):
        await _ws_send_safe(ws, {"type": "auth_failed", "reason": "invalid_token"})
        await _ws_close_safe(ws, code=4401)
        return False

    await _ws_send_safe(ws, {"type": "auth_ok", "ts": time.time()})
    return True


async def _ws_send_safe(ws: Any, payload: dict) -> None:
    try:
        await ws.send(json.dumps(payload))
    except Exception:  # noqa: BLE001
        pass


async def _ws_close_safe(ws: Any, code: int = 1008) -> None:
    try:
        await ws.close(code=code)
    except Exception:  # noqa: BLE001
        pass


# ── Public API ───────────────────────────────────────────────────────────────

__all__ = [
    "TOKEN_ENV_VAR",
    "AUTH_FLAG_ENV",
    "AUTH_HEADER",
    "EXEMPT_PATH_PREFIXES",
    "IPCAuthError",
    "ensure_token",
    "token_file_path",
    "is_auth_enabled",
    "is_exempt_path",
    "validate_token",
    "check_rest_request",
    "authenticate_ws",
]
