from __future__ import annotations

import json
import re
from typing import Any

import structlog

from core.beta.storage import StorageService
from core.data_collection.redactor import redact_record

from .schema import HtlggEnvelope

log = structlog.get_logger(__name__)

_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class HtlggRedisBridge:
    def __init__(
        self,
        *,
        redis_client: Any = None,
        storage: StorageService | None = None,
        ttl_sec: int = 300,
        stream_ttl_sec: int = 900,
        stream_maxlen: int = 1000,
    ) -> None:
        self.redis = redis_client
        self.storage = storage
        self.ttl_sec = ttl_sec
        self.stream_ttl_sec = stream_ttl_sec
        self.stream_maxlen = stream_maxlen

    def publish(self, topic: str, envelope: HtlggEnvelope, encoded: str) -> None:
        payload = redact_record(
            {
                "version": envelope.version,
                "topic": topic,
                "record": encoded,
                "session_id": envelope.session_id,
                "task_id": envelope.task_id,
                "timestamp": envelope.timestamp,
                "metadata": envelope.metadata,
            }
        )
        serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        user = _KEY_SAFE.sub("_", envelope.user_id)[:80] or "local"
        session = _KEY_SAFE.sub("_", envelope.session_id)[:80] or "session"
        latest_key = f"htlgg:{user}:{session}:{topic}"
        stream_key = f"htlgg:{user}:{session}:stream"
        try:
            if self.redis is not None:
                self.redis.setex(latest_key, self.ttl_sec, serialized)
                self.redis.xadd(
                    stream_key,
                    {"payload": serialized},
                    maxlen=self.stream_maxlen,
                    approximate=True,
                )
                self.redis.expire(stream_key, self.stream_ttl_sec)
                return
        except Exception as exc:
            log.warning("htlgg.redis_degraded", error=str(exc))
        if self.storage is not None:
            self.storage.add_agent_event(
                task_id=None,
                event_type=f"htlgg.{topic}",
                payload=payload,
            )


__all__ = ["HtlggRedisBridge"]
