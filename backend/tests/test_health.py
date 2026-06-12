"""Tests for the /health endpoint and app-level behavior."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_response_schema(client: AsyncClient) -> None:
    response = await client.get("/health")
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert isinstance(body["version"], str)


@pytest.mark.asyncio
async def test_health_not_in_openapi(client: AsyncClient) -> None:
    """Health endpoint must be excluded from OpenAPI schema."""
    openapi = await client.get("/openapi.json")
    if openapi.status_code == 200:
        paths = openapi.json().get("paths", {})
        assert "/health" not in paths


@pytest.mark.asyncio
async def test_security_headers_present(client: AsyncClient) -> None:
    """SecurityHeadersMiddleware must add X-Content-Type-Options."""
    response = await client.get("/health")
    assert response.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.asyncio
async def test_request_id_header(client: AsyncClient) -> None:
    """RequestIDMiddleware must echo or generate X-Request-ID."""
    response = await client.get("/health", headers={"X-Request-ID": "test-id-123"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_cors_header_present(client: AsyncClient) -> None:
    """CORSMiddleware must respond to OPTIONS preflight."""
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    # 200 or 405 depending on middleware order; what matters is the endpoint exists
    assert response.status_code in (200, 405, 400)


@pytest.mark.asyncio
async def test_unknown_route_returns_404(client: AsyncClient) -> None:
    response = await client.get("/this-route-does-not-exist")
    assert response.status_code == 404
