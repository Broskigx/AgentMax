"""
License manager -- validates Whop licenses at startup and every 30 minutes.

Three-layer protection strategy
---------------------------------
Layer 1  PyArmor obfuscates this file at build time (unreadable bytecode).
Layer 2  The HMAC token derived here is verified inside the compiled C++
         PixelEngine binary. A cracked Python layer still can't unlock the engine
         without also cracking the C++ binary.
Layer 3  This module's heartbeat polls Whop every 30 minutes; a revoked or
         refunded license causes a graceful shutdown mid-task.

Environment variables
---------------------
AGENTMAX_LICENSE   -- the user's Whop license key (required at runtime)
AGENTMAX_WHOP_KEY  -- your company's Whop API key (embed at build time)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac
import json
import os
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

# ── Shared HMAC secret (matches license_guard.hpp) ──────────────────────────
# Same 32 bytes used by the C++ verify side.
# PyArmor encrypts this bytecode; the C++ binary XOR-obfuscates the same value.
_HMAC_SECRET = bytes(
    [
        0x4A,
        0x6D,
        0x78,
        0x21,
        0x9F,
        0x3C,
        0x5B,
        0xA2,
        0xE1,
        0x74,
        0x08,
        0xCD,
        0x56,
        0x89,
        0xF3,
        0x2E,
        0x91,
        0x45,
        0xBC,
        0x67,
        0xD0,
        0x13,
        0x7A,
        0xEF,
        0x28,
        0x94,
        0x51,
        0xC6,
        0x0D,
        0x82,
        0xF5,
        0x39,
    ]
)

HEARTBEAT_INTERVAL_SEC: int = 30 * 60  # 30 minutes
_MAX_CONSECUTIVE_FAILURES: int = 3  # network failures before shutdown
_WHOP_API_BASE = "https://api.whop.com/api/v2"

# Grace-period cache so one network blip doesn't kill a long-running task
_CACHE_PATH = Path("./data/license_cache.json")


@dataclass
class LicenseInfo:
    valid: bool
    plan: str = ""
    user_email: str = ""
    expires_at: float | None = None


class LicenseManager:
    def __init__(self, config: Any) -> None:
        self._config = config
        self._key: str = os.environ.get("AGENTMAX_LICENSE", "")
        self._whop_api_key: str = os.environ.get("AGENTMAX_WHOP_KEY", "")
        self._valid: bool = False
        self._cpp_token: str = ""
        self._heartbeat_task: asyncio.Task | None = None
        self._failures: int = 0
        self._shutdown_cb: Callable[..., Coroutine] | None = None
        self._degraded: bool = False  # set by anti_tamper watchdog
        self._machine_id: str = ""  # set on first Whop call

    # ── Public API ────────────────────────────────────────────────────────────

    async def validate_startup(self) -> bool:
        """
        Must be awaited before agents start.
        Returns False (and logs an error) when the key is missing or invalid.
        Falls back to a cached valid state (max 24 h) on transient network errors.
        """
        if not self._key:
            log.error(
                "license.missing_key",
                hint="Set AGENTMAX_LICENSE env var to your Whop license key.",
            )
            return False

        # Derive the machine fingerprint once; it's included in every Whop call
        # so Whop can track how many machines are using this license.
        try:
            from core.security.anti_tamper import get_machine_fingerprint

            self._machine_id = get_machine_fingerprint()
        except Exception:
            self._machine_id = ""

        info = await self._call_whop()
        if info.valid:
            self._valid = True
            self._cpp_token = self._derive_token()
            self._save_cache(info)
            log.info("license.ok", plan=info.plan, email=info.user_email)
            return True

        # Network error path -- try local cache
        cached = self._load_cache()
        if cached and cached.get("valid") and self._cache_still_fresh(cached):
            self._valid = True
            self._cpp_token = self._derive_token()
            log.warning("license.using_cache", reason="Whop API unreachable at startup")
            return True

        log.error("license.invalid_or_expired", key=self._key[:8] + "…")
        return False

    async def start_heartbeat(self, shutdown_cb: Callable[..., Coroutine] | None = None) -> None:
        """Launch the 30-minute background heartbeat. Pass an async shutdown callback."""
        self._shutdown_cb = shutdown_cb
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="license-heartbeat")

    async def stop(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

    def inject_into_cpp(self, engine: Any) -> bool:
        """
        Call after the C++ PixelEngine is constructed.
        Returns True when the engine accepted the token.
        """
        if not self._valid or not self._cpp_token:
            return False
        ok: bool = engine.init_license(self._key, self._cpp_token)
        if not ok:
            # Clock drift beyond ±1 h: regenerate with fresh hour bucket
            self._cpp_token = self._derive_token()
            ok = engine.init_license(self._key, self._cpp_token)
        if ok:
            log.info("license.cpp_engine_unlocked")
        else:
            log.error("license.cpp_token_rejected")
        return ok

    @property
    def is_valid(self) -> bool:
        return self._valid

    @property
    def cpp_token(self) -> str:
        return self._cpp_token

    # ── Internal ──────────────────────────────────────────────────────────────

    def _derive_token(self) -> str:
        hour = int(time.time()) // 3600
        msg = f"{self._key}:{hour}".encode()
        return _hmac.new(_HMAC_SECRET, msg, hashlib.sha256).hexdigest()

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

            # Degradation: if tamper was detected, add a random extra delay
            # before the heartbeat so the timing becomes unpredictable.
            if self._degraded:
                import random

                await asyncio.sleep(random.uniform(0, 30))

            info = await self._call_whop()

            if info.valid:
                self._failures = 0
                # Refresh the C++ token every heartbeat (token rotates hourly)
                self._cpp_token = self._derive_token()
                log.debug("license.heartbeat_ok")
                continue

            if info.valid is False and info.plan == "__invalid__":
                # Whop explicitly said the license is revoked/refunded
                log.error("license.REVOKED -- shutting down")
                self._valid = False
                self._cpp_token = ""
                if self._shutdown_cb:
                    await self._shutdown_cb("license_revoked")
                return

            # Transient network error
            self._failures += 1
            log.warning(
                "license.heartbeat_network_error",
                consecutive_failures=self._failures,
                max=_MAX_CONSECUTIVE_FAILURES,
            )
            if self._failures >= _MAX_CONSECUTIVE_FAILURES:
                log.error("license.too_many_failures -- shutting down")
                self._valid = False
                self._cpp_token = ""
                if self._shutdown_cb:
                    await self._shutdown_cb("license_network_failure")
                return

    async def _call_whop(self) -> LicenseInfo:
        """Validate `self._key` against Whop API, bypassing the air-gap socket patch."""
        try:
            from core.security.air_gap import _ORIGINAL_CREATE_CONNECTION  # noqa: F401
        except ImportError:
            pass  # air_gap module not loaded yet -- normal sockets available

        try:
            return await _whop_http_check(
                self._key, self._whop_api_key, machine_id=self._machine_id
            )
        except Exception as exc:
            log.warning("license.whop_request_failed", error=str(exc))
            # Sentinel: plan="" means we don't know (network error), not revoked
            return LicenseInfo(valid=False, plan="__network_error__")

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _save_cache(self, info: LicenseInfo) -> None:
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_PATH.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "plan": info.plan,
                        "user_email": info.user_email,
                        "cached_at": time.time(),
                    }
                )
            )
        except Exception:
            pass

    def _load_cache(self) -> dict | None:
        try:
            return json.loads(_CACHE_PATH.read_text())
        except Exception:
            return None

    @staticmethod
    def _cache_still_fresh(cache: dict) -> bool:
        max_age = 24 * 3600  # 24-hour grace
        return (time.time() - cache.get("cached_at", 0)) < max_age


# ── Whop HTTP client (runs with original sockets, bypasses air-gap) ──────────


async def _whop_http_check(license_key: str, api_key: str, machine_id: str = "") -> LicenseInfo:
    """
    Calls the Whop v2 membership API.
    Uses original sockets directly so air-gap mode cannot block it.
    Set AGENTMAX_WHOP_KEY to your company's Whop API key.

    machine_id is sent as a query parameter so Whop records which machine
    activated this license. In your Whop dashboard / webhook you can flag
    licenses used from too many distinct machines (license sharing).
    """
    # Temporarily restore real sockets for this call
    import socket as _socket

    from core.security import air_gap

    _orig_cc = _socket.create_connection
    _orig_ga = _socket.getaddrinfo

    if air_gap.is_active():
        _socket.create_connection = air_gap._ORIGINAL_CREATE_CONNECTION  # type: ignore[assignment]
        _socket.getaddrinfo = air_gap._ORIGINAL_GETADDRINFO  # type: ignore[assignment]

    try:
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Include machine fingerprint as metadata so Whop logs it.
        # You can set up Whop webhooks to alert when a single license
        # is activated from more than N distinct machine_ids.
        params: dict[str, str] = {}
        if machine_id:
            params["metadata[machine_id]"] = machine_id

        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{_WHOP_API_BASE}/memberships/{license_key}",
                headers=headers,
                params=params,
            )

        if resp.status_code == 404:
            return LicenseInfo(valid=False, plan="__invalid__")

        if resp.status_code == 200:
            data = resp.json()
            status: str = data.get("status", "")
            return LicenseInfo(
                valid=status == "active",
                plan=data.get("plan", {}).get("name", status),
                user_email=data.get("user", {}).get("email", ""),
            )

        # 401/403 → bad API key (config error, not user error)
        if resp.status_code in (401, 403):
            log.error("license.bad_api_key", status=resp.status_code)
            return LicenseInfo(valid=False, plan="__invalid__")

        # 5xx → transient server error
        return LicenseInfo(valid=False, plan="__network_error__")

    finally:
        if air_gap.is_active():
            _socket.create_connection = _orig_cc  # type: ignore[assignment]
            _socket.getaddrinfo = _orig_ga  # type: ignore[assignment]
