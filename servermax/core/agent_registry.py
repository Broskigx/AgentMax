"""
AgentRegistry — tracks connected AgentMax instances.

Agents register on startup and send heartbeats every 30 s. Any agent
that misses 3 consecutive heartbeats is marked offline (but not removed).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from servermax.models.schemas import AgentInfo, AgentRegisterRequest

_HEARTBEAT_TIMEOUT_S = 90.0  # 3 x 30 s
_CLEANUP_INTERVAL_S = 60.0


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentInfo] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="agent-registry-cleanup")

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------ #
    # Registration                                                         #
    # ------------------------------------------------------------------ #

    async def register(self, req: AgentRegisterRequest) -> AgentInfo:
        now = time.time()
        info = AgentInfo(
            agent_id=req.agent_id,
            hostname=req.hostname,
            platform=req.platform,
            version=req.version,
            capabilities=req.capabilities,
            registered_at=now,
            last_seen=now,
            online=True,
        )
        async with self._lock:
            self._agents[req.agent_id] = info
        return info

    async def unregister(self, agent_id: str) -> bool:
        async with self._lock:
            return self._agents.pop(agent_id, None) is not None

    # ------------------------------------------------------------------ #
    # Heartbeat                                                            #
    # ------------------------------------------------------------------ #

    async def heartbeat(self, agent_id: str, status: str = "idle", lesson_count: int = 0) -> bool:  # noqa: ARG002
        async with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return False
            agent.last_seen = time.time()
            agent.online = True
        return True

    # ------------------------------------------------------------------ #
    # Query                                                                #
    # ------------------------------------------------------------------ #

    async def list_all(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [a.model_dump() for a in self._agents.values()]

    async def list_online(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [a.model_dump() for a in self._agents.values() if a.online]

    async def count_online(self) -> int:
        async with self._lock:
            return sum(1 for a in self._agents.values() if a.online)

    async def get(self, agent_id: str) -> dict[str, Any] | None:
        async with self._lock:
            a = self._agents.get(agent_id)
            return a.model_dump() if a else None

    # ------------------------------------------------------------------ #
    # Cleanup loop                                                         #
    # ------------------------------------------------------------------ #

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL_S)
            cutoff = time.time() - _HEARTBEAT_TIMEOUT_S
            async with self._lock:
                for agent in self._agents.values():
                    if agent.last_seen < cutoff:
                        agent.online = False
