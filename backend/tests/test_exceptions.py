"""Tests for the exception hierarchy and domain error handlers."""

from __future__ import annotations

import pytest
from backend.core.exceptions import (
    AdminAuthRequired,
    AgentMaxError,
    AuthError,
    DeviceLimitExceeded,
    IntegrityViolation,
    InvalidChallenge,
    InvalidChallengeResponse,
    InvalidToken,
    LicenseError,
    LicenseExpired,
    LicenseNotFound,
    LicenseRevoked,
    LicenseSuspended,
    PermissionDenied,
    RateLimitExceeded,
    SessionExpired,
    SessionInvalidated,
    SessionNotFound,
    TokenExpired,
)
from fastapi import status

# ── Exception hierarchy ───────────────────────────────────────────────────────


def test_all_errors_inherit_AGENTMAX_error() -> None:
    leaf_classes = [
        LicenseNotFound,
        LicenseExpired,
        LicenseRevoked,
        LicenseSuspended,
        DeviceLimitExceeded,
        InvalidToken,
        TokenExpired,
        InvalidChallenge,
        InvalidChallengeResponse,
        SessionNotFound,
        SessionExpired,
        SessionInvalidated,
        RateLimitExceeded,
        AdminAuthRequired,
        PermissionDenied,
        IntegrityViolation,
    ]
    for cls in leaf_classes:
        instance = cls("test message")
        assert isinstance(instance, AgentMaxError), f"{cls.__name__} must inherit AgentMaxError"


def test_license_errors_inherit_license_error() -> None:
    for cls in (
        LicenseNotFound,
        LicenseExpired,
        LicenseRevoked,
        LicenseSuspended,
        DeviceLimitExceeded,
    ):
        assert issubclass(cls, LicenseError)


def test_auth_errors_inherit_auth_error() -> None:
    for cls in (
        InvalidToken,
        TokenExpired,
        InvalidChallenge,
        InvalidChallengeResponse,
        SessionNotFound,
        SessionExpired,
        SessionInvalidated,
    ):
        assert issubclass(cls, AuthError)


# ── HTTP status codes ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc_cls,expected_status",
    [
        (LicenseNotFound, status.HTTP_404_NOT_FOUND),
        (LicenseExpired, status.HTTP_402_PAYMENT_REQUIRED),
        (LicenseRevoked, status.HTTP_403_FORBIDDEN),
        (LicenseSuspended, status.HTTP_403_FORBIDDEN),
        (DeviceLimitExceeded, status.HTTP_409_CONFLICT),
        (AuthError, status.HTTP_401_UNAUTHORIZED),
        (InvalidToken, status.HTTP_401_UNAUTHORIZED),
        (TokenExpired, status.HTTP_401_UNAUTHORIZED),
        (SessionNotFound, status.HTTP_404_NOT_FOUND),
        (SessionExpired, status.HTTP_401_UNAUTHORIZED),
        (SessionInvalidated, status.HTTP_401_UNAUTHORIZED),
        (RateLimitExceeded, status.HTTP_429_TOO_MANY_REQUESTS),
        (AdminAuthRequired, status.HTTP_401_UNAUTHORIZED),
        (PermissionDenied, status.HTTP_403_FORBIDDEN),
        (IntegrityViolation, status.HTTP_400_BAD_REQUEST),
        (AgentMaxError, status.HTTP_500_INTERNAL_SERVER_ERROR),
    ],
)
def test_http_status_codes(exc_cls, expected_status) -> None:
    exc = exc_cls("test")
    assert exc.http_status == expected_status, (
        f"{exc_cls.__name__}.http_status should be {expected_status}, got {exc.http_status}"
    )


# ── Error codes ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc_cls,expected_code",
    [
        (LicenseNotFound, "license_not_found"),
        (LicenseExpired, "license_expired"),
        (LicenseRevoked, "license_revoked"),
        (LicenseSuspended, "license_suspended"),
        (DeviceLimitExceeded, "device_limit_exceeded"),
        (InvalidToken, "invalid_token"),
        (TokenExpired, "token_expired"),
        (InvalidChallenge, "invalid_challenge"),
        (InvalidChallengeResponse, "invalid_challenge_response"),
        (SessionNotFound, "session_not_found"),
        (SessionExpired, "session_expired"),
        (SessionInvalidated, "session_invalidated"),
        (RateLimitExceeded, "rate_limit_exceeded"),
        (AdminAuthRequired, "admin_auth_required"),
        (PermissionDenied, "permission_denied"),
        (IntegrityViolation, "integrity_violation"),
    ],
)
def test_error_codes(exc_cls, expected_code) -> None:
    exc = exc_cls("test")
    assert exc.error_code == expected_code


# ── Message and detail ───────────────────────────────────────────────────────


def test_exception_stores_message() -> None:
    exc = LicenseNotFound("License ABC not found")
    assert exc.message == "License ABC not found"
    assert str(exc) == "License ABC not found"


def test_exception_detail_defaults_to_message() -> None:
    exc = LicenseExpired("Your license has expired")
    assert exc.detail == exc.message


def test_exception_custom_detail() -> None:
    exc = LicenseRevoked("License revoked", detail="Revoked due to ToS violation")
    assert exc.message == "License revoked"
    assert exc.detail == "Revoked due to ToS violation"


def test_empty_message() -> None:
    exc = AgentMaxError()
    assert exc.message == ""


# ── Exception handler behavior (via client) ───────────────────────────────────


@pytest.mark.asyncio
async def test_domain_error_returned_as_json(client) -> None:
    """The /health endpoint must return JSON, not HTML."""
    response = await client.get("/health")
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.asyncio
async def test_404_returns_json(client) -> None:
    response = await client.get("/nonexistent-endpoint-xyz")
    assert response.status_code == 404
    # FastAPI returns JSON by default
    assert "application/json" in response.headers.get("content-type", "")
