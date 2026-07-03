"""Optional Redis service with SQLite-backed degraded fallback."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .config import BetaConfig, get_beta_config
from .storage import StorageService


@dataclass
class RedisStatus:
    available: bool
    degraded: bool
    message: str


class RedisService:
    def __init__(
        self,
        redis_url: str | None = None,
        *,
        config: BetaConfig | None = None,
        storage: StorageService | None = None,
    ) -> None:
        self.config = config or get_beta_config()
        self.redis_url = redis_url or self.config.redis_url
        self.storage = storage or StorageService(config=self.config)
        self.client: Any = None
        self._available = False
        self._message = "not connected"

    def connect(self) -> RedisStatus:
        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(self.redis_url, decode_responses=True, socket_timeout=1)
            client.ping()
            self.client = client
            self._available = True
            self._message = "connected"
        except Exception as exc:  # noqa: BLE001 - degraded mode is expected for beta
            self.client = None
            self._available = False
            self._message = f"degraded: {exc}"
        return self.status()

    def status(self) -> RedisStatus:
        return RedisStatus(self._available, not self._available, self._message)

    def isAvailable(self) -> bool:  # noqa: N802 - public interface requested in plan
        return self._available

    def heartbeat(self) -> RedisStatus:
        self._ensure()
        if self.client:
            try:
                self.client.ping()
                self._available = True
                self._message = "connected"
            except Exception as exc:  # noqa: BLE001 - degraded mode is expected for beta
                self.client = None
                self._available = False
                self._message = f"degraded: {exc}"
        return self.status()

    def _ensure(self) -> None:
        if self.client is None and self._message == "not connected":
            self.connect()

    def setTaskState(self, task_id: str, state: dict[str, Any], ttl_sec: int = 3600) -> None:  # noqa: N802
        self._ensure()
        if self.client:
            self.client.setex(f"task:{task_id}:state", ttl_sec, json.dumps(state))
            return
        self.storage.set_setting(f"redis_fallback.task_state.{task_id}", state)
        if "status" in state:
            try:
                self.storage.update_task_status(task_id, str(state["status"]), result=state)
            except Exception:
                pass

    def getTaskState(self, task_id: str) -> dict[str, Any] | None:  # noqa: N802
        self._ensure()
        if self.client:
            raw = self.client.get(f"task:{task_id}:state")
            return json.loads(raw) if raw else None
        return self.storage.get_setting(f"redis_fallback.task_state.{task_id}")

    def enqueueTask(self, queue_name: str, task: dict[str, Any]) -> None:  # noqa: N802
        self._ensure()
        if self.client:
            self.client.rpush(f"queue:{queue_name}", json.dumps(task))
            return
        key = f"redis_fallback.queue.{queue_name}"
        queue = self.storage.get_setting(key, default=[]) or []
        queue.append(task)
        self.storage.set_setting(key, queue)

    def dequeueTask(self, queue_name: str) -> dict[str, Any] | None:  # noqa: N802
        self._ensure()
        if self.client:
            raw = self.client.lpop(f"queue:{queue_name}")
            return json.loads(raw) if raw else None
        key = f"redis_fallback.queue.{queue_name}"
        queue = self.storage.get_setting(key, default=[]) or []
        if not queue:
            return None
        task = queue.pop(0)
        self.storage.set_setting(key, queue)
        return task

    def acquireLock(self, name: str, owner: str, ttl_sec: int = 30) -> bool:  # noqa: N802
        self._ensure()
        key = f"lock:{name}"
        if self.client:
            return bool(self.client.set(key, owner, nx=True, ex=ttl_sec))
        lock = self.storage.get_setting(f"redis_fallback.{key}")
        now = time.time()
        if lock and float(lock.get("expires_at", 0)) > now:
            return False
        self.storage.set_setting(
            f"redis_fallback.{key}", {"owner": owner, "expires_at": now + ttl_sec}
        )
        return True

    def releaseLock(self, name: str, owner: str) -> bool:  # noqa: N802
        self._ensure()
        key = f"lock:{name}"
        if self.client:
            if self.client.get(key) == owner:
                self.client.delete(key)
                return True
            return False
        lock_key = f"redis_fallback.{key}"
        lock = self.storage.get_setting(lock_key)
        if lock and lock.get("owner") == owner:
            self.storage.set_setting(lock_key, {})
            return True
        return False

    def rateLimitCheck(self, key: str, limit: int = 60, window_sec: int = 60) -> bool:  # noqa: N802
        self._ensure()
        now = time.time()
        bucket = f"rate:{key}"
        if self.client:
            pipe = self.client.pipeline()
            pipe.zremrangebyscore(bucket, "-inf", now - window_sec)
            pipe.zcard(bucket)
            pipe.zadd(bucket, {str(now): now})
            pipe.expire(bucket, window_sec + 1)
            _, count, _, _ = pipe.execute()
            return int(count) < int(limit)
        storage_key = f"redis_fallback.{bucket}"
        events = [
            float(ts)
            for ts in (self.storage.get_setting(storage_key, default=[]) or [])
            if now - float(ts) <= window_sec
        ]
        allowed = len(events) < limit
        if allowed:
            events.append(now)
            self.storage.set_setting(storage_key, events)
        return allowed
