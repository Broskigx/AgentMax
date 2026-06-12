"""Runtime discovery endpoints used by the desktop beta."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_connectors_endpoint_returns_runtime_connectors(client: AsyncClient) -> None:
    response = await client.get("/v1/connectors")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["count"] >= 1
    assert isinstance(body["connectors"], list)
    assert any(item["id"] == "AgentMax-runtime" for item in body["connectors"])


@pytest.mark.asyncio
async def test_pending_approvals_endpoint_is_empty_list(client: AsyncClient) -> None:
    response = await client.get("/v1/approvals/pending")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["count"] == 0
    assert body["approvals"] == []


@pytest.mark.asyncio
async def test_telemetry_stats_endpoint_is_safe_summary(client: AsyncClient) -> None:
    response = await client.get("/v1/telemetry/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["privacy"] == "payloads_not_returned"
