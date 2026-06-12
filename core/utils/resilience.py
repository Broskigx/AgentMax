"""
Circuit breaker + exponential backoff for external API calls.

Usage
-----
# Retry with backoff (decorator):
@retry_async(max_attempts=4, base_delay=1.0, max_delay=30.0)
async def call_license_server(): ...

# Circuit breaker (instance, one per remote endpoint):
_breaker = CircuitBreaker(fail_threshold=5, reset_timeout=60)

async def call_api():
    async with _breaker:
        return await http.post(...)

# Combine both:
@retry_async(max_attempts=3)
async def safe_call():
    async with _breaker:
        return await http.post(...)

# Legacy guard pattern (core/ai compatibility):
async with breaker.guard("service-name") as guard:
    result = await call()
"""

from __future__ import annotations

import asyncio
import functools
import random
import time
from collections.abc import Callable, Coroutine
from enum import Enum, auto
from typing import Any, TypeVar

import structlog

log = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


# ── Exceptions ────────────────────────────────────────────────────────────────


class CircuitOpenError(RuntimeError):
    """Raised when a call is blocked by an open circuit."""


class BreakerOpen(CircuitOpenError):
    """Raised when a call is rejected because the circuit is open."""


class RetryExhausted(Exception):
    """Raised when all retry attempts have failed."""

    def __init__(self, last_error: Exception, attempts: int) -> None:
        super().__init__(f"All {attempts} attempts failed: {last_error}")
        self.last_error = last_error
        self.attempts = attempts


# ── Retry with exponential backoff ────────────────────────────────────────────


def retry_async(
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.25,
    retryable: tuple[type[Exception], ...] = (Exception,),
    give_up_on: tuple[type[Exception], ...] = (),
) -> Callable[[F], F]:
    """
    Decorator that retries an async function with exponential backoff.

    Args:
        max_attempts: Total attempts (1 = no retry).
        base_delay:   Delay after the first failure in seconds.
        max_delay:    Upper bound on any single delay.
        jitter:       Fraction of delay added as random jitter (±jitter * delay).
        retryable:    Exception types that trigger a retry. Default: all.
        give_up_on:   Exception types that abort immediately (no retry).
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception = RuntimeError("no attempts made")
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except give_up_on as exc:
                    log.warning(
                        "retry.give_up",
                        fn=fn.__qualname__,
                        attempt=attempt,
                        error=str(exc),
                    )
                    raise
                except retryable as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    delay *= 1 + random.uniform(-jitter, jitter)
                    log.warning(
                        "retry.attempt_failed",
                        fn=fn.__qualname__,
                        attempt=attempt,
                        max=max_attempts,
                        retry_in_s=f"{delay:.2f}",
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
            raise RetryExhausted(last_exc, max_attempts)

        return wrapper  # type: ignore[return-value]

    return decorator


# ── Circuit breaker ────────────────────────────────────────────────────────────


class BreakerState(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreaker:
    """
    Async-safe circuit breaker for a single remote dependency.

    States:
      CLOSED    -> calls pass through; consecutive failures counted.
      OPEN      -> calls fail immediately with BreakerOpen; after
                    `reset_timeout` seconds, transitions to HALF_OPEN.
      HALF_OPEN -> one probe call allowed; success -> CLOSED,
                    failure -> OPEN (with fresh timeout).

    Use one instance per endpoint so different services can fail
    independently without affecting each other.
    """

    def __init__(
        self,
        fail_threshold: int | None = None,
        reset_timeout: float | None = None,
        half_open_probes: int | None = None,
        name: str = "unnamed",
        **kwargs: Any,
    ) -> None:
        # Backward-compatible aliases from core/ai/circuit_breaker.py
        fail_threshold = kwargs.pop("failure_threshold", fail_threshold)
        reset_timeout = kwargs.pop("recovery_timeout", reset_timeout)
        half_open_probes = kwargs.pop("half_open_max", half_open_probes)
        if kwargs:
            import warnings
            warnings.warn(f"Unknown CircuitBreaker kwargs: {set(kwargs)}", stacklevel=2)

        self.name = name
        self._fail_threshold = fail_threshold if fail_threshold is not None else 5
        self._reset_timeout = reset_timeout if reset_timeout is not None else 60.0
        self._half_open_probes = half_open_probes if half_open_probes is not None else 1

        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()
        self._probe_count = 0

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == BreakerState.OPEN

    async def guard(self, service: str | None = None) -> _CircuitBreakerGuard:
        """Legacy guard pattern -- returns an async context manager.

        Compatible with core/ai usage::

            async with cb.guard("claude-api"):
                result = await client.call(...)
        """
        return _CircuitBreakerGuard(self, service or self.name)

    async def _check_allow(self) -> None:
        async with self._lock:
            if self._state == BreakerState.OPEN:
                elapsed = time.monotonic() - (self._opened_at or 0)
                if elapsed >= self._reset_timeout:
                    self._state = BreakerState.HALF_OPEN
                    self._probe_count = 0
                    log.info("circuit_breaker.half_open", name=self.name)
                else:
                    remaining = self._reset_timeout - elapsed
                    raise BreakerOpen(f"Circuit '{self.name}' is OPEN -- retry in {remaining:.0f}s")

            if self._state == BreakerState.HALF_OPEN:
                if self._probe_count >= self._half_open_probes:
                    raise BreakerOpen(f"Circuit '{self.name}' is HALF_OPEN -- probe in progress")
                self._probe_count += 1

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state != BreakerState.CLOSED:
                log.info("circuit_breaker.closed", name=self.name, after_failures=self._failures)
            self._failures = 0
            self._opened_at = None
            self._state = BreakerState.CLOSED

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._failures >= self._fail_threshold or self._state == BreakerState.HALF_OPEN:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                log.error(
                    "circuit_breaker.opened",
                    name=self.name,
                    failures=self._failures,
                    reset_in_s=self._reset_timeout,
                )

    async def __aenter__(self) -> CircuitBreaker:
        await self._check_allow()
        return self

    async def __aexit__(
        self,
        exc_type: type | None,
        exc: BaseException | None,
        tb: Any,
    ) -> bool:
        if exc_type is None:
            await self._on_success()
        elif not isinstance(exc, BreakerOpen):
            await self._on_failure()
        return False


class _CircuitBreakerGuard:
    """Async context manager returned by CircuitBreaker.guard()."""

    def __init__(self, cb: CircuitBreaker, service: str) -> None:
        self._cb = cb
        self._service = service

    async def __aenter__(self) -> _CircuitBreakerGuard:
        await self._cb._check_allow()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc is None:
            await self._cb._on_success()
        elif not isinstance(exc, BreakerOpen):
            await self._cb._on_failure()
        return False


# Module-level default instance shared by Claude client
_default_cb: CircuitBreaker | None = None


def get_claude_circuit_breaker() -> CircuitBreaker:
    global _default_cb
    if _default_cb is None:
        _default_cb = CircuitBreaker(fail_threshold=5, reset_timeout=60.0, name="claude")
    return _default_cb
