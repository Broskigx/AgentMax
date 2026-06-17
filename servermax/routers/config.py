"""Config router — agent config distribution."""

from __future__ import annotations

from fastapi import APIRouter, Request

from servermax.models.schemas import ServerConfig

router = APIRouter(prefix="/config")


@router.get("", response_model=ServerConfig)
async def get_config(request: Request) -> dict:
    return request.app.state.config.get()
