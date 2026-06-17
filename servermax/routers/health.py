"""Health router — GET /health"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

from servermax.models.schemas import HealthResponse

if TYPE_CHECKING:
    from servermax.core.agent_registry import AgentRegistry

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    registry: AgentRegistry = request.app.state.registry
    start_time: float = request.app.state.start_time
    return HealthResponse(
        ok=True,
        version=request.app.state.config.get().get("version", "0.1.1"),
        agents_online=await registry.count_online(),
        uptime_s=round(time.time() - start_time, 1),
    )
