"""
/v1/license -- client-facing license endpoints.

POST /v1/license/heartbeat   -- 30-min keepalive
GET  /v1/license/info        -- current license details
GET  /v1/license/verify      -- lightweight validity check (no DB hit)
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from backend.core.database import get_db
from backend.core.security import verify_token
from backend.schemas.auth import HeartbeatRequest, HeartbeatResponse
from backend.services.license_service import LicenseService
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/license", tags=["license"])


async def _require_token(
    authorization: Annotated[str, Header()],
) -> dict:
    """Dependency -- validate Bearer token, return payload."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "auth_error", "message": "Bearer token required"},
        )
    return verify_token(authorization[7:])


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    body: HeartbeatRequest,
    request: Request,
    token: dict = Depends(_require_token),
    db: AsyncSession = Depends(get_db),
) -> HeartbeatResponse:
    """
    30-minute heartbeat endpoint.
    Client must call this every 30 minutes to confirm license validity.
    If the server returns valid=False, client should stop operations and prompt user.
    """
    if token.get("mfp") != body.machine_fingerprint:
        log.warning(
            "heartbeat.machine_fp_mismatch",
            token_mfp=token.get("mfp", "")[:8],
            request_mfp=body.machine_fingerprint[:8],
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "integrity_violation", "message": "Machine fingerprint mismatch"},
        )

    svc = LicenseService(db)
    result = await svc.heartbeat(
        license_id=uuid.UUID(token["sub"]),
        session_id=uuid.UUID(token["ses"]),
        machine_fingerprint=body.machine_fingerprint,
        ip_address=(
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or (request.client.host if request.client else None)
        ),
        token_jti=token.get("jti", ""),
    )
    return HeartbeatResponse(**result)


@router.get("/verify")
async def verify(
    token: dict = Depends(_require_token),
) -> dict:
    """
    Lightweight token verification -- no DB round-trip.
    Ed25519 signature check only.  Good for quick app-start validation.
    """
    return {
        "valid": True,
        "plan": token.get("pln"),
        "license_id": token.get("sub"),
        "expires_in": max(0, token.get("exp", 0) - __import__("time").time()),
    }
