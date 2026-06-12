"""
/v1/auth -- authentication endpoints.

POST /v1/auth/challenge    -- initiate ECDH challenge
POST /v1/auth/activate     -- complete challenge, get tokens
POST /v1/auth/refresh      -- rotate refresh token
POST /v1/auth/logout       -- invalidate session
"""

from __future__ import annotations

import uuid

import structlog
from backend.core.database import get_db
from backend.core.security import verify_token
from backend.schemas.auth import (
    ActivateRequest,
    ActivateResponse,
    ChallengeRequest,
    ChallengeResponse,
    LogoutResponse,
    RefreshRequest,
    RefreshResponse,
)
from backend.services.crypto_service import create_challenge
from backend.services.license_service import LicenseService
from backend.services.session_service import SessionService
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@router.post("/challenge", response_model=ChallengeResponse, status_code=200)
async def challenge(
    body: ChallengeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ChallengeResponse:
    """
    Step 1 of activation: request an ECDH challenge.

    Rate limit: 10 req/min per IP (enforced by middleware).
    """
    log.info(
        "auth.challenge_requested",
        key_prefix=body.license_key[:6],
        machine_fp=body.machine_fingerprint[:8],
    )
    data = await create_challenge(
        license_key=body.license_key,
        machine_fingerprint=body.machine_fingerprint,
    )
    return ChallengeResponse(**data)


@router.post("/activate", response_model=ActivateResponse, status_code=200)
async def activate(
    body: ActivateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ActivateResponse:
    """
    Step 2 of activation: submit ECDH response, receive tokens.
    """
    svc = LicenseService(db)
    resp = await svc.activate(
        challenge_id=body.challenge_id,
        client_ephemeral_public=body.client_ephemeral_public,
        response_hex=body.response,
        machine_fingerprint=body.machine_fingerprint,
        device_info=body.device_info.model_dump(),
        client_version="",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    log.info("auth.activated", license_id=str(resp.license.id)[:8])
    return resp


@router.post("/refresh", response_model=RefreshResponse, status_code=200)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    """
    Rotate refresh token and issue a new access token.
    Implements refresh-token rotation -- reuse of an old token revokes the session.
    """
    svc = SessionService(db)
    result = await svc.refresh(
        encrypted_refresh_token=body.refresh_token,
        machine_fingerprint=body.machine_fingerprint,
    )
    return RefreshResponse(**result, token_type="Bearer")


@router.post("/logout", response_model=LogoutResponse, status_code=200)
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    """Invalidate the current session."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return LogoutResponse(ok=True)  # no token -- nothing to invalidate

    try:
        payload = verify_token(auth[7:])
        session_id = uuid.UUID(payload["ses"])
        svc = SessionService(db)
        await svc.logout(session_id)
    except Exception:
        pass  # always succeed -- idempotent

    return LogoutResponse(ok=True)
