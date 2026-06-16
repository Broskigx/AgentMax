"""Token budget management for AgentMax."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional


class TokenLimitError(Exception):
    """Raised when a token/credit budget is exhausted."""


class UnlimitedTokenError(Exception):
    """Raised on unexpected operations against an unlimited token manager."""


@dataclass
class TokenConfig:
    """Per-manager budget configuration."""

    daily_cap: int = 0      # 0 = unlimited
    unlimited: bool = False

    def is_unlimited(self) -> bool:
        return self.unlimited or self.daily_cap <= 0


@dataclass
class TokenUsage:
    """Snapshot of token consumption for a (plan, user) pair."""

    plan: str
    user: str
    consumed: int
    cap: int
    remaining: int  # -1 means unlimited


class TokenManager:
    """Thread-safe per-(plan, user) daily token budget manager."""

    def __init__(self, config: TokenConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._usage: dict[tuple[str, str], int] = {}

    @property
    def unlimited(self) -> bool:
        return self._config.is_unlimited()

    def consume(self, plan: str, user: str, delta: int = 1) -> TokenUsage:
        """Consume *delta* tokens; raises TokenLimitError if cap exceeded."""
        with self._lock:
            key = (plan, user)
            current = self._usage.get(key, 0)
            if not self.unlimited and current + delta > self._config.daily_cap:
                raise TokenLimitError(
                    f"Daily token cap of {self._config.daily_cap} exceeded "
                    f"(plan={plan!r}, user={user!r}, consumed={current}, requested={delta})"
                )
            self._usage[key] = current + delta
            consumed = self._usage[key]
        cap = self._config.daily_cap
        return TokenUsage(
            plan=plan,
            user=user,
            consumed=consumed,
            cap=cap,
            remaining=max(0, cap - consumed) if cap > 0 else -1,
        )

    def check(self, plan: str, user: str, delta: int = 1) -> None:
        """Raise TokenLimitError if consuming *delta* would exceed the cap (no-op if unlimited)."""
        if self.unlimited:
            return
        with self._lock:
            current = self._usage.get((plan, user), 0)
        if current + delta > self._config.daily_cap:
            raise TokenLimitError(
                f"Daily token cap of {self._config.daily_cap} exceeded "
                f"(plan={plan!r}, user={user!r})"
            )

    def usage(self, plan: str, user: str) -> TokenUsage:
        """Return current usage snapshot for a (plan, user) pair."""
        with self._lock:
            consumed = self._usage.get((plan, user), 0)
        cap = self._config.daily_cap
        return TokenUsage(
            plan=plan,
            user=user,
            consumed=consumed,
            cap=cap,
            remaining=max(0, cap - consumed) if cap > 0 else -1,
        )

    def reset(self, plan: Optional[str] = None, user: Optional[str] = None) -> None:
        """Reset usage counters (call at daily rollover)."""
        with self._lock:
            if plan is None and user is None:
                self._usage.clear()
            else:
                to_remove = [
                    k for k in self._usage
                    if (plan is None or k[0] == plan) and (user is None or k[1] == user)
                ]
                for k in to_remove:
                    del self._usage[k]

    def snapshot(self) -> dict:
        """JSON-serializable snapshot for IPC reporting."""
        with self._lock:
            entries = list(self._usage.items())
        cap = self._config.daily_cap
        return {
            "available": True,
            "unlimited": self.unlimited,
            "daily_cap": cap,
            "entries": [
                {
                    "plan": plan,
                    "user": user,
                    "consumed": consumed,
                    "remaining": max(0, cap - consumed) if cap > 0 else -1,
                }
                for (plan, user), consumed in entries
            ],
        }


def create_token_manager(config) -> TokenManager:
    """Factory used by the runtime to boot the token system.

    Accepts a local TokenConfig or a core.config.TokenConfig
    (which only carries an *unlimited* flag).
    """
    if isinstance(config, TokenConfig):
        return TokenManager(config)
    unlimited = bool(getattr(config, "unlimited", False))
    daily_cap = int(getattr(config, "daily_cap", 0))
    return TokenManager(TokenConfig(daily_cap=daily_cap, unlimited=unlimited))
