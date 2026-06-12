"""
AgentMax License Backend -- FastAPI entry point.

Startup sequence
----------------
1. Validate settings
2. Connect database + run pending Alembic migrations
3. Connect Redis pool
4. Mount routers
5. Start background cleanup task (expired sessions, stale challenges)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC

import orjson
import structlog
from backend.core.config import get_settings
from backend.core.database import close_db, init_db
from backend.core.exceptions import AgentMaxError
from backend.core.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from backend.core.redis_pool import close_redis, get_redis
from backend.routers import admin, auth, license, runtime, telemetry, updates
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

_settings = get_settings()

# security router is mounted only in non-production (mock/dev endpoints only)
if not _settings.is_production:
    from backend.routers import security

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(serializer=orjson.dumps)
        if _settings.is_production
        else structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(10 if not _settings.is_production else 20),
)

log = structlog.get_logger(__name__)


# ── Background tasks ──────────────────────────────────────────────────────────


async def _cleanup_loop() -> None:
    """Hourly cleanup of expired sessions and stale challenges."""
    from datetime import datetime

    from backend.core.database import AsyncSessionLocal
    from backend.models.session import Session, SessionStatus
    from sqlalchemy import update

    while True:
        await asyncio.sleep(3600)
        try:
            now = datetime.now(tz=UTC)
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(Session)
                    .where(Session.expires_at < now, Session.status == SessionStatus.ACTIVE)
                    .values(status=SessionStatus.EXPIRED, invalidated_at=now)
                )
                await db.commit()
            log.info("cleanup.completed")
        except Exception as exc:
            log.error("cleanup.failed", error=str(exc))


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log.info("backend.starting", environment=_settings.environment)
    await init_db()
    _ = get_redis()  # warm up pool
    cleanup_task = asyncio.create_task(_cleanup_loop(), name="session-cleanup")
    log.info("backend.ready")
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await close_redis()
    await close_db()
    log.info("backend.stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AgentMax License API",
    version="1.0.0",
    docs_url="/docs" if not _settings.is_production else None,
    redoc_url="/redoc" if not _settings.is_production else None,
    openapi_url="/openapi.json" if not _settings.is_production else None,
    lifespan=lifespan,
)

# Middleware (order matters -- outermost applied last)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Secret", "X-Request-ID"],
)

# Routers
app.include_router(auth.router)
app.include_router(license.router)
if not _settings.is_production:
    app.include_router(security.router)
app.include_router(admin.router)
app.include_router(runtime.router)
app.include_router(telemetry.router)
app.include_router(updates.router)


# ── Exception handlers ────────────────────────────────────────────────────────


@app.exception_handler(AgentMaxError)
async def domain_error_handler(request: Request, exc: AgentMaxError) -> JSONResponse:
    log.warning(
        "domain_error",
        error_code=exc.error_code,
        status=exc.http_status,
        message=exc.message,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": exc.error_code, "message": exc.message},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "An unexpected error occurred"},
    )


# ── Health check ──────────────────────────────────────────────────────────────


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=_settings.host,
        port=_settings.port,
        workers=_settings.workers if _settings.is_production else 1,
        reload=not _settings.is_production,
        log_config=None,  # structlog handles logging
    )
