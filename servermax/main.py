"""
ServerMax — central coordination server for AgentMax fleet.

Runs on port 7791.  Start with:
    uvicorn servermax.main:app --host 0.0.0.0 --port 7791 --reload

Environment variables:
    SERVERMAX_SECRET   Bearer token agents must send (optional, skipped if unset)
    ANTHROPIC_API_KEY  Forwarded to AIRouter for Claude backend
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from servermax.core.agent_registry import AgentRegistry
from servermax.core.ai_router import AIRouter
from servermax.core.config_manager import ConfigManager
from servermax.routers import agents, ai, config, health, learn

logging.basicConfig(level=logging.INFO)
log = structlog.get_logger()

_SECRET = os.environ.get("SERVERMAX_SECRET", "")


# ── Lifespan ─────────────────────────────────────────────────────────────── #

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = ConfigManager()
    cfg.load()

    registry = AgentRegistry()
    await registry.start()

    ai_router = AIRouter()
    await ai_router.start(cfg.get().get("ai_backends", []))

    app.state.config = cfg
    app.state.registry = registry
    app.state.ai_router = ai_router
    app.state.start_time = time.time()

    log.info("servermax.started", port=7791)
    yield

    await registry.stop()
    await ai_router.stop()
    log.info("servermax.stopped")


# ── App ───────────────────────────────────────────────────────────────────── #

app = FastAPI(
    title="ServerMax",
    version="0.1.1",
    description="Central coordination server for AgentMax fleet",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth middleware ───────────────────────────────────────────────────────── #

@app.middleware("http")
async def auth_middleware(request: Request, call_next) -> Response:
    if _SECRET and request.url.path not in ("/health", "/docs", "/openapi.json", "/redoc"):
        token = request.headers.get("Authorization", "")
        if token != f"Bearer {_SECRET}":
            return Response(content='{"detail":"Unauthorized"}', status_code=401, media_type="application/json")
    return await call_next(request)


# ── Routers ───────────────────────────────────────────────────────────────── #

app.include_router(health.router)
app.include_router(agents.router)
app.include_router(ai.router)
app.include_router(learn.router)
app.include_router(config.router)
