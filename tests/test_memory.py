"""Tests for short-term memory."""

import time
from unittest.mock import MagicMock

from core.memory.short_term import ShortTermMemory


def make_config(capacity=10, ttl=60):
    cfg = MagicMock()
    cfg.short_term_capacity = capacity
    cfg.short_term_ttl_sec = ttl
    return cfg


class TestShortTermMemory:
    def test_put_get(self):
        mem = ShortTermMemory(make_config())
        mem.put("key1", {"data": 42})
        assert mem.get("key1") == {"data": 42}

    def test_missing_key_returns_default(self):
        mem = ShortTermMemory(make_config())
        assert mem.get("nonexistent") is None
        assert mem.get("nonexistent", "fallback") == "fallback"

    def test_ttl_expiry(self):
        mem = ShortTermMemory(make_config(ttl=1))
        mem.put("expire", "value", ttl=0.01)
        time.sleep(0.02)
        assert mem.get("expire") is None

    def test_capacity_lru_eviction(self):
        mem = ShortTermMemory(make_config(capacity=3))
        mem.put("a", 1)
        mem.put("b", 2)
        mem.put("c", 3)
        mem.put("d", 4)  # should evict "a" (least recently used)
        assert mem.get("a") is None
        assert mem.get("b") == 2
        assert mem.get("d") == 4

    def test_update_refreshes_lru(self):
        mem = ShortTermMemory(make_config(capacity=3))
        mem.put("a", 1)
        mem.put("b", 2)
        mem.put("c", 3)
        mem.get("a")  # access "a" to make it recently used
        mem.put("d", 4)  # should evict "b" now
        assert mem.get("a") == 1
        assert mem.get("b") is None

    def test_delete(self):
        mem = ShortTermMemory(make_config())
        mem.put("x", 99)
        mem.delete("x")
        assert mem.get("x") is None
