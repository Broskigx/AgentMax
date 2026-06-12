"""
Production middleware stack.

- RequestIDMiddleware  -- injects X-Request-ID into every request/response
- RateLimitMiddleware  -- Redis sliding-window rate limiting per IP
- SecurityHeadersMiddleware -- HSTS, X-Content-Type-Options, etc.
"""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from .config import get_settings
from .exceptions import RateLimitExceeded
from .redis_pool import check_rate_limit

_settings = get_settings()
log = structlog.get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        start = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request_id
        log.info(
            "http.request",
            status=response.status_code,
            elapsed_ms=round(elapsed_ms, 2),
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-IP rate limiting for public and protected routes.

    Auth endpoints:  rate_limit_auth_per_minute   (strict)
    API endpoints:   rate_limit_api_per_minute     (generous)
    Admin endpoints: rate_limit_admin_per_minute   (very generous)
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._limits = {
            "/v1/auth": (_settings.rate_limit_auth_per_minute, 60),
            "/v1/admin": (_settings.rate_limit_admin_per_minute, 60),
            "/v1/license": (_settings.rate_limit_api_per_minute, 60),
        }

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        peer_ip = request.client.host if request.client else "unknown"
        if peer_ip in _settings.trusted_proxies and request.headers.get("X-Forwarded-For"):
            client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        else:
            client_ip = peer_ip

        limit, window = self._resolve_limit(request.url.path)
        bucket_key = f"{client_ip}:{request.url.path.split('/')[2] if request.url.path.count('/') >= 2 else 'root'}"

        allowed = await check_rate_limit(bucket_key, limit, window)
        if not allowed:
            log.warning("rate_limit.exceeded", client_ip=client_ip, path=request.url.path)
            raise RateLimitExceeded("Too many requests -- please slow down")

        return await call_next(request)

    def _resolve_limit(self, path: str) -> tuple[int, int]:
        for prefix, config in self._limits.items():
            if path.startswith(prefix):
                return config
        return _settings.rate_limit_api_per_minute, 60


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        if _settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response
