"""Redis connection pool with typed helpers."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis

from .config import get_settings

_settings = get_settings()

_pool: Redis | None = None


def get_redis() -> Redis:
    """Return the shared async Redis client (no connection on call)."""
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            str(_settings.redis_url),
            max_connections=_settings.redis_max_connections,
            decode_responses=True,
        )
    return _pool


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


# ── Typed helpers ─────────────────────────────────────────────────────────────


async def redis_set_json(key: str, value: Any, ttl: int) -> None:
    await get_redis().setex(key, ttl, json.dumps(value))


async def redis_get_json(key: str) -> Any | None:
    raw = await get_redis().get(key)
    return json.loads(raw) if raw is not None else None


async def redis_delete(key: str) -> None:
    await get_redis().delete(key)


async def redis_exists(key: str) -> bool:
    return bool(await get_redis().exists(key))


# ── Sliding window rate limiter ───────────────────────────────────────────────
# Uses Redis sorted set.  Score = timestamp (float seconds).
# Window: last `window_sec` seconds.  Max `limit` requests per window.

_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local cutoff = now - window
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= limit then
    return 0
end
redis.call('ZADD', key, now, now .. ':' .. math.random(1, 1000000))
redis.call('EXPIRE', key, window + 1)
return 1
"""


async def check_rate_limit(key: str, limit: int, window_sec: int) -> bool:
    """
    Returns True when the request is within the rate limit.
    Returns False when the limit is exceeded (caller should raise 429).
    """
    import time

    r = get_redis()
    now = time.time()
    result = await r.eval(  # type: ignore[attr-defined]
        _RATE_LIMIT_SCRIPT,
        1,
        f"rl:{key}",
        now,
        window_sec,
        limit,
    )
    return bool(result)
