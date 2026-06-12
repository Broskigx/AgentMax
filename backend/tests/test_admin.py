"""Tests for /v1/admin endpoints -- auth guard and basic behavior."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

ADMIN_SECRET = "test-admin-secret-for-testing-only-32!"
WRONG_SECRET = "definitely-wrong-secret"


# ── Auth guard ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_no_secret_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/admin/metrics")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_wrong_secret_returns_401(client: AsyncClient) -> None:
    response = await client.get(
        "/v1/admin/metrics",
        headers={"X-Admin-Secret": WRONG_SECRET},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_wrong_secret_error_body(client: AsyncClient) -> None:
    response = await client.get(
        "/v1/admin/metrics",
        headers={"X-Admin-Secret": WRONG_SECRET},
    )
    body = response.json()
    assert "error" in body or "detail" in body


# ── Metrics ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_metrics_with_valid_secret(client: AsyncClient) -> None:
    mock_metrics = {
        "total_licenses": 42,
        "active_licenses": 38,
        "expired_licenses": 2,
        "revoked_licenses": 2,
        "total_activations": 100,
        "active_sessions": 15,
        "new_licenses_24h": 3,
        "new_activations_24h": 5,
    }
    with patch(
        "backend.services.license_service.LicenseService.get_metrics",
        new_callable=AsyncMock,
        return_value=mock_metrics,
    ):
        response = await client.get(
            "/v1/admin/metrics",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["total_licenses"] == 42
    assert body["active_licenses"] == 38


@pytest.mark.asyncio
async def test_admin_metrics_schema(client: AsyncClient) -> None:
    mock_metrics = {
        "total_licenses": 0,
        "active_licenses": 0,
        "expired_licenses": 0,
        "revoked_licenses": 0,
        "total_activations": 0,
        "active_sessions": 0,
        "new_licenses_24h": 0,
        "new_activations_24h": 0,
    }
    with patch(
        "backend.services.license_service.LicenseService.get_metrics",
        new_callable=AsyncMock,
        return_value=mock_metrics,
    ):
        response = await client.get(
            "/v1/admin/metrics",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
    assert response.status_code == 200
    body = response.json()
    required_keys = {
        "total_licenses",
        "active_licenses",
        "expired_licenses",
        "revoked_licenses",
        "total_activations",
        "active_sessions",
        "new_licenses_24h",
        "new_activations_24h",
    }
    assert required_keys.issubset(body.keys())


# ── Audit log ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_audit_no_secret(client: AsyncClient) -> None:
    response = await client.get("/v1/admin/audit")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_audit_returns_list(client: AsyncClient) -> None:
    with patch("backend.core.database.get_db") as mock_get_db:
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await client.get(
            "/v1/admin/audit",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# ── Plans ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_plan_requires_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/admin/plans",
        json={
            "name": "pro",
            "display_name": "Pro Plan",
            "max_devices": 3,
            "duration_days": 30,
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_plan_validation_name_pattern(client: AsyncClient) -> None:
    """Plan name must match ^[a-z0-9_]+$."""
    response = await client.post(
        "/v1/admin/plans",
        headers={"X-Admin-Secret": ADMIN_SECRET},
        json={
            "name": "INVALID NAME WITH SPACES",
            "display_name": "Invalid Plan",
            "max_devices": 1,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_plan_validation_max_devices_min(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/admin/plans",
        headers={"X-Admin-Secret": ADMIN_SECRET},
        json={
            "name": "test_plan",
            "display_name": "Test Plan",
            "max_devices": 0,  # must be >= 1
        },
    )
    assert response.status_code == 422


# ── Licenses ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_licenses_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/v1/admin/licenses")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_license_invalid_email(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/admin/licenses",
        headers={"X-Admin-Secret": ADMIN_SECRET},
        json={
            "plan_id": str(uuid.uuid4()),
            "user_email": "not-an-email",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_revoke_license_missing_reason(client: AsyncClient) -> None:
    """Revoking without a reason must fail validation."""
    license_id = str(uuid.uuid4())
    response = await client.post(
        f"/v1/admin/licenses/{license_id}/revoke",
        headers={"X-Admin-Secret": ADMIN_SECRET},
        json={},  # missing required 'reason' field
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_revoke_license_reason_too_short(client: AsyncClient) -> None:
    """Reason must be at least 4 characters."""
    license_id = str(uuid.uuid4())
    response = await client.post(
        f"/v1/admin/licenses/{license_id}/revoke",
        headers={"X-Admin-Secret": ADMIN_SECRET},
        json={"reason": "ab"},
    )
    assert response.status_code == 422


# ── Sessions ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sessions_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/v1/admin/sessions")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_stats_timeline_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/v1/admin/stats/timeline")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_stats_timeline_days_validation(client: AsyncClient) -> None:
    """days param must be between 1 and 365."""
    response = await client.get(
        "/v1/admin/stats/timeline?days=0",
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert response.status_code == 422

    response = await client.get(
        "/v1/admin/stats/timeline?days=999",
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    assert response.status_code == 422
