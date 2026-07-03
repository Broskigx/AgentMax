"""Typed exception hierarchy for AgentMax backend."""

from __future__ import annotations

from fastapi import status


class AgentMaxError(Exception):
    """Root exception -- all domain errors derive from this."""

    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"

    def __init__(self, message: str = "", *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or message


# ── License ───────────────────────────────────────────────────────────────────


class LicenseError(AgentMaxError):
    http_status = status.HTTP_403_FORBIDDEN
    error_code = "license_error"


class LicenseNotFound(LicenseError):
    http_status = status.HTTP_404_NOT_FOUND
    error_code = "license_not_found"


class LicenseExpired(LicenseError):
    http_status = status.HTTP_402_PAYMENT_REQUIRED
    error_code = "license_expired"


class LicenseRevoked(LicenseError):
    http_status = status.HTTP_403_FORBIDDEN
    error_code = "license_revoked"


class LicenseSuspended(LicenseError):
    http_status = status.HTTP_403_FORBIDDEN
    error_code = "license_suspended"


class DeviceLimitExceeded(LicenseError):
    http_status = status.HTTP_409_CONFLICT
    error_code = "device_limit_exceeded"


# ── Auth / Session ────────────────────────────────────────────────────────────


class AuthError(AgentMaxError):
    http_status = status.HTTP_401_UNAUTHORIZED
    error_code = "auth_error"


class InvalidToken(AuthError):
    error_code = "invalid_token"


class TokenExpired(AuthError):
    error_code = "token_expired"


class InvalidChallenge(AuthError):
    error_code = "invalid_challenge"


class InvalidChallengeResponse(AuthError):
    error_code = "invalid_challenge_response"


class SessionNotFound(AuthError):
    http_status = status.HTTP_404_NOT_FOUND
    error_code = "session_not_found"


class SessionExpired(AuthError):
    error_code = "session_expired"


class SessionInvalidated(AuthError):
    error_code = "session_invalidated"


# ── Rate limiting ─────────────────────────────────────────────────────────────


class RateLimitExceeded(AgentMaxError):
    http_status = status.HTTP_429_TOO_MANY_REQUESTS
    error_code = "rate_limit_exceeded"


# ── Admin ─────────────────────────────────────────────────────────────────────


class AdminAuthRequired(AgentMaxError):
    http_status = status.HTTP_401_UNAUTHORIZED
    error_code = "admin_auth_required"


class PermissionDenied(AgentMaxError):
    http_status = status.HTTP_403_FORBIDDEN
    error_code = "permission_denied"


# ── Integrity ─────────────────────────────────────────────────────────────────


class IntegrityViolation(AgentMaxError):
    http_status = status.HTTP_400_BAD_REQUEST
    error_code = "integrity_violation"
