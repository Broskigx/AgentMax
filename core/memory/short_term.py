"""Short-term memory -- LRU cache with TTL for current session context."""

from __future__ import annotations

import time
from collections import OrderedDict
from threading import RLock
from typing import Any


class ShortTermMemory:
    """
    Thread-safe LRU cache with per-entry TTL.

    Used for:
      - Current task context
      - Recent OCR results per region
      - Recent action outcomes
      - Element location cache
    """

    def __init__(self, config: Any) -> None:
        self._capacity = config.short_term_capacity
        self._ttl = config.short_term_ttl_sec
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = RLock()

    def put(self, key: str, value: Any, ttl: float | None = None) -> None:
        expires = time.monotonic() + (ttl or self._ttl)
        with self._lock:
            # Proactively evict expired entries every 50 writes to keep memory lean.
            if len(self._store) % 50 == 0:
                now = time.monotonic()
                expired_keys = [k for k, (_, exp) in self._store.items() if now > exp]
                for k in expired_keys:
                    del self._store[k]
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expires)
            if len(self._store) > self._capacity:
                self._store.popitem(last=False)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key not in self._store:
                return default
            value, expires = self._store[key]
            if time.monotonic() > expires:
                del self._store[key]
                return default
            self._store.move_to_end(key)
            return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def evict_expired(self) -> int:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
            return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())
