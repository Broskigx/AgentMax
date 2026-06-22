"""
Session service -- manages the lifecycle of client sessions.

Session lifecycle
-----------------
ACTIVE  →  EXPIRED    (refresh_token TTL exceeded)
ACTIVE  →  REVOKED    (admin revocation / license revoked)
ACTIVE  →  LOGGED_OUT (client explicit logout)

Refresh token rotation: every refresh issues a new refresh_token and
invalidates the previous one (detect token theft via reuse).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from backend.core.config import get_settings
from backend.core.exceptions import (
    InvalidToken,
    SessionExpired,
    SessionInvalidated,
)
from backend.core.security import create_access_token
from backend.models.session import Session, SessionStatus
from backend.services.crypto_service import (
    decrypt_refresh_token,
    encrypt_refresh_token,
    generate_refresh_token,
    hash_refresh_token,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)
_settings = get_settings()


class SessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def refresh(
        self,
        *,
        encrypted_refresh_token: str,
        machine_fingerprint: str,
    ) -> dict:
        """
        Validate refresh token, rotate it, and issue a new access token.
        Implements refresh-token rotation to detect token theft.
        """
        try:
            plaintext = decrypt_refresh_token(encrypted_refresh_token)
        except Exception as exc:
            raise InvalidToken("Cannot decrypt refresh token") from exc

        token_hash = hash_refresh_token(plaintext)

        result = await self._db.execute(
            select(Session).where(Session.refresh_token_hash == token_hash)
        )
        session = result.scalar_one_or_none()

        if session is None:
            # Possible token reuse -- a hash we've never seen might mean
            # the token was already rotated and someone is replaying an old one.
            log.warning("session.refresh_token_not_found", hash_prefix=token_hash[:8])
            raise InvalidToken("Refresh token not found")

        if session.status == SessionStatus.REVOKED:
            raise SessionInvalidated("Session has been revoked")
        if session.status == SessionStatus.LOGGED_OUT:
            raise SessionInvalidated("Session has been logged out")

        now = datetime.now(tz=UTC)
        if now > session.expires_at:
            await self._mark_expired(session)
            raise SessionExpired("Refresh token has expired -- re-activate license")

        if session.machine_fingerprint != machine_fingerprint:
            # Machine changed -- could be VM cloning or theft
            log.warning(
                "session.machine_fingerprint_changed",
                session_id=str(session.id),
                stored=session.machine_fingerprint[:8],
                received=machine_fingerprint[:8],
            )
            await self.invalidate(session.id, reason="machine_fingerprint_changed")
            raise SessionInvalidated("Machine fingerprint changed -- re-activate license")

        # Rotate refresh token
        new_plain, new_hash = generate_refresh_token()
        new_expiry = now + timedelta(seconds=_settings.refresh_token_ttl_seconds)
        new_jti = str(uuid.uuid4())

        await self._db.execute(
            update(Session)
            .where(Session.id == session.id)
            .values(
                refresh_token_hash=new_hash,
                access_token_jti=new_jti,
                expires_at=new_expiry,
                last_heartbeat=now,
            )
        )

        access_token = create_access_token(
            license_id=str(session.license_id),
            session_id=str(session.id),
            activation_id=str(session.activation_id),
            machine_fingerprint=machine_fingerprint,
            plan=session.plan,
            extra_claims={"jti": new_jti},
        )

        return {
            "access_token": access_token,
            "refresh_token": encrypt_refresh_token(new_plain),
            "expires_in": _settings.access_token_ttl_seconds,
        }

    async def touch(self, session_id: uuid.UUID) -> None:
        """Update last_heartbeat timestamp."""
        await self._db.execute(
            update(Session)
            .where(Session.id == session_id, Session.status == SessionStatus.ACTIVE)
            .values(last_heartbeat=datetime.now(tz=UTC))
        )

    async def invalidate(self, session_id: uuid.UUID, reason: str = "") -> None:
        now = datetime.now(tz=UTC)
        await self._db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                status=SessionStatus.REVOKED,
                invalidated_at=now,
                invalidation_reason=reason,
            )
        )

    async def logout(self, session_id: uuid.UUID) -> None:
        now = datetime.now(tz=UTC)
        await self._db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                status=SessionStatus.LOGGED_OUT,
                invalidated_at=now,
                invalidation_reason="user_logout",
            )
        )

    async def invalidate_by_activation(self, activation_id: uuid.UUID, reason: str = "") -> None:
        now = datetime.now(tz=UTC)
        await self._db.execute(
            update(Session)
            .where(
                Session.activation_id == activation_id,
                Session.status == SessionStatus.ACTIVE,
            )
            .values(
                status=SessionStatus.REVOKED,
                invalidated_at=now,
                invalidation_reason=reason,
            )
        )

    async def invalidate_by_license(self, license_id: uuid.UUID, reason: str = "") -> None:
        now = datetime.now(tz=UTC)
        await self._db.execute(
            update(Session)
            .where(
                Session.license_id == license_id,
                Session.status == SessionStatus.ACTIVE,
            )
            .values(
                status=SessionStatus.REVOKED,
                invalidated_at=now,
                invalidation_reason=reason,
            )
        )

    async def get(self, session_id: uuid.UUID) -> Session | None:
        result = await self._db.execute(select(Session).where(Session.id == session_id))
        return result.scalar_one_or_none()

    async def get_active_for_license(self, license_id: uuid.UUID) -> list[Session]:
        result = await self._db.execute(
            select(Session).where(
                Session.license_id == license_id,
                Session.status == SessionStatus.ACTIVE,
            )
        )
        return list(result.scalars().all())

    async def _mark_expired(self, session: Session) -> None:
        await self._db.execute(
            update(Session)
            .where(Session.id == session.id)
            .values(status=SessionStatus.EXPIRED, invalidated_at=datetime.now(tz=UTC))
        )
