"""AI router — chat completion and backend status."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from servermax.models.schemas import AIBackendInfo, AIChatRequest, AIChatResponse

router = APIRouter(prefix="/ai")


@router.post("/chat", response_model=AIChatResponse)
async def chat(body: AIChatRequest, request: Request) -> AIChatResponse:
    ai_router = request.app.state.ai_router
    t0 = time.monotonic()
    try:
        content, backend_name = await ai_router.chat(
            messages=body.messages,
            system=body.system,
            model=body.model,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    latency_ms = int((time.monotonic() - t0) * 1000)
    return AIChatResponse(
        content=content,
        model=body.model,
        backend=backend_name,
        latency_ms=latency_ms,
    )


@router.get("/backends", response_model=list[AIBackendInfo])
async def backends(request: Request) -> list[dict]:
    return request.app.state.ai_router.status()
