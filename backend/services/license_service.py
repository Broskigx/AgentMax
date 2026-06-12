"""
License service -- core business logic.

Responsibilities
----------------
- Validate license key and compute effective status
- Record device activations (enforce max_devices limit)
- Issue access/refresh/offline tokens after successful challenge
- Handle heartbeat (refresh session, detect anomalies)
- Admin CRUD operations on licenses and plans
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from backend.core.config import get_settings
from backend.core.exceptions import (
    DeviceLimitExceeded,
    LicenseExpired,
    LicenseNotFound,
    LicenseRevoked,
    LicenseSuspended,
)
from backend.core.security import (
    create_access_token,
    create_offline_token,
)
from backend.models.license import Activation, License, LicenseStatus, Plan
from backend.models.session import Session, SessionStatus
from backend.schemas.auth import ActivateResponse, LicenseSummary
from backend.services.audit_service import AuditService
from backend.services.crypto_service import (
    encrypt_refresh_token,
    generate_refresh_token,
    verify_challenge_response,
)
from backend.services.session_service import SessionService
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

log = structlog.get_logger(__name__)
_settings = get_settings()


class LicenseService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._audit = AuditService(db)
        self._sessions = SessionService(db)

    # ── Public API ────────────────────────────────────────────────────────────

    async def activate(
        self,
        *,
        challenge_id: uuid.UUID,
        client_ephemeral_public: str,
        response_hex: str,
        machine_fingerprint: str,
        device_info: dict,
        client_version: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> ActivateResponse:
        """
        Complete the challenge-response activation flow.

        1. Verify ECDH response
        2. Load and validate license
        3. Enforce device limits
        4. Upsert activation record
        5. Create session
        6. Issue tokens
        """
        # Step 1: verify ECDH
        stored = await verify_challenge_response(
            challenge_id=challenge_id,
            client_ephemeral_public=client_ephemeral_public,
            response_hex=response_hex,
            machine_fingerprint=machine_fingerprint,
        )
        license_key: str = stored["license_key"]

        # Step 2: load & validate license
        lic = await self._get_license_by_key(license_key)
        plan = await self._get_plan(lic.plan_id)
        self._assert_license_valid(lic)

        # Step 3: device limit
        active_activations = await self._count_active_activations(lic.id)
        activation = await self._find_activation(lic.id, machine_fingerprint)
        if activation is None and active_activations >= plan.max_devices:
            raise DeviceLimitExceeded(
                f"License allows at most {plan.max_devices} device(s). "
                "Deactivate an existing device before activating a new one."
            )

        # Step 4: upsert activation
        activation = await self._upsert_activation(
            license_id=lic.id,
            machine_fingerprint=machine_fingerprint,
            device_info=device_info,
            client_version=client_version,
        )

        # Step 5: invalidate any existing session for this activation
        await self._sessions.invalidate_by_activation(
            activation_id=activation.id, reason="new_activation"
        )

        # Step 6: create session + tokens
        refresh_token_plain, refresh_token_hash = generate_refresh_token()

        now = datetime.now(tz=UTC)
        session = Session(
            license_id=lic.id,
            activation_id=activation.id,
            status=SessionStatus.ACTIVE,
            refresh_token_hash=refresh_token_hash,
            access_token_jti=str(uuid.uuid4()),  # placeholder -- updated below
            expires_at=now + timedelta(seconds=_settings.refresh_token_ttl_seconds),
            machine_fingerprint=machine_fingerprint,
            plan=plan.name,
            ip_address=ip_address,
            user_agent=user_agent,
            last_heartbeat=now,
        )
        self._db.add(session)
        await self._db.flush()  # get session.id

        access_token = create_access_token(
            license_id=str(lic.id),
            session_id=str(session.id),
            activation_id=str(activation.id),
            machine_fingerprint=machine_fingerprint,
            plan=plan.name,
            ttl=_settings.access_token_ttl_seconds,
        )
        offline_token = create_offline_token(
            license_id=str(lic.id),
            machine_fingerprint=machine_fingerprint,
            plan=plan.name,
            expires_at=lic.expires_at.timestamp() if lic.expires_at else None,
        )

        await self._audit.log(
            event_type="license.activation",
            license_id=lic.id,
            session_id=session.id,
            activation_id=activation.id,
            machine_fingerprint=machine_fingerprint,
            ip_address=ip_address,
            message=f"License activated: key={license_key[:6]}… plan={plan.name}",
            payload={"client_version": client_version, **device_info},
        )

        return ActivateResponse(
            access_token=access_token,
            refresh_token=encrypt_refresh_token(refresh_token_plain),
            offline_token=offline_token,
            expires_in=_settings.access_token_ttl_seconds,
            license=LicenseSummary(
                id=lic.id,
                key=lic.key,
                plan=plan.name,
                status=lic.status.value,
                expires_at=lic.expires_at,
                max_devices=plan.max_devices,
            ),
        )

    async def heartbeat(
        self,
        *,
        license_id: uuid.UUID,
        session_id: uuid.UUID,
        machine_fingerprint: str,
        ip_address: str | None,
    ) -> dict[str, Any]:
        """
        30-minute heartbeat -- verify license still active and refresh session state.
        """
        lic = await self._get_license(license_id)

        # Check effective status (may have expired since last check)
        effective_status = self._effective_status(lic)
        if effective_status != LicenseStatus.ACTIVE:
            await self._audit.log(
                event_type="license.heartbeat_rejected",
                license_id=lic.id,
                session_id=session_id,
                machine_fingerprint=machine_fingerprint,
                ip_address=ip_address,
                message=f"Heartbeat rejected: {effective_status.value}",
                severity="warning",
            )
            return {
                "valid": False,
                "status": effective_status.value,
                "plan": "",
                "next_heartbeat_in": 0,
                "server_time": int(datetime.now(tz=UTC).timestamp()),
            }

        # Update last_heartbeat
        await self._sessions.touch(session_id)

        plan = await self._get_plan(lic.plan_id)
        return {
            "valid": True,
            "status": "active",
            "plan": plan.name,
            "next_heartbeat_in": 1800,
            "server_time": int(datetime.now(tz=UTC).timestamp()),
        }

    # ── Admin operations ──────────────────────────────────────────────────────

    async def create_license(self, plan_id: uuid.UUID, **kwargs: Any) -> License:
        from backend.core.security import secure_license_key

        plan = await self._get_plan(plan_id)
        lic = License(
            key=secure_license_key(),
            plan_id=plan_id,
            status=LicenseStatus.ACTIVE,
            **kwargs,
        )
        self._db.add(lic)
        await self._db.flush()
        await self._audit.log(
            event_type="admin.license_created",
            license_id=lic.id,
            message=f"License created: {lic.key} plan={plan.name}",
        )
        return lic

    async def revoke_license(self, license_id: uuid.UUID, reason: str) -> None:
        now = datetime.now(tz=UTC)
        await self._db.execute(
            update(License)
            .where(License.id == license_id)
            .values(
                status=LicenseStatus.REVOKED,
                revoked_at=now,
                revocation_reason=reason,
                updated_at=now,
            )
        )
        # Invalidate all active sessions immediately
        await self._sessions.invalidate_by_license(license_id, reason="license_revoked")
        await self._audit.log(
            event_type="admin.license_revoked",
            license_id=license_id,
            message=f"License revoked: {reason}",
            severity="warning",
        )

    async def list_licenses(
        self,
        *,
        status: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[License], int]:
        from sqlalchemy import or_

        filters = []
        if status:
            filters.append(License.status == LicenseStatus(status))
        if q:
            pat = f"%{q}%"
            filters.append(
                or_(
                    License.key.ilike(pat),
                    License.user_email.ilike(pat),
                    License.user_name.ilike(pat),
                )
            )

        count_q = select(func.count()).select_from(License)
        if filters:
            count_q = count_q.where(*filters)
        total = await self._db.scalar(count_q)

        data_q = (
            select(License)
            .options(selectinload(License.plan), selectinload(License.activations))
            .order_by(License.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if filters:
            data_q = data_q.where(*filters)

        result = await self._db.execute(data_q)
        return list(result.scalars().all()), total or 0

    async def get_metrics(self) -> dict[str, int]:
        from datetime import timedelta

        now = datetime.now(tz=UTC)
        yesterday = now - timedelta(hours=24)

        total = await self._db.scalar(select(func.count()).select_from(License))
        active = await self._db.scalar(
            select(func.count()).select_from(License).where(License.status == LicenseStatus.ACTIVE)
        )
        expired = await self._db.scalar(
            select(func.count()).select_from(License).where(License.status == LicenseStatus.EXPIRED)
        )
        revoked = await self._db.scalar(
            select(func.count()).select_from(License).where(License.status == LicenseStatus.REVOKED)
        )
        new_24h = await self._db.scalar(
            select(func.count()).select_from(License).where(License.created_at >= yesterday)
        )
        total_activations = await self._db.scalar(select(func.count()).select_from(Activation))
        new_act_24h = await self._db.scalar(
            select(func.count()).select_from(Activation).where(Activation.created_at >= yesterday)
        )
        active_sessions = await self._db.scalar(
            select(func.count()).select_from(Session).where(Session.status == SessionStatus.ACTIVE)
        )

        return {
            "total_licenses": total or 0,
            "active_licenses": active or 0,
            "expired_licenses": expired or 0,
            "revoked_licenses": revoked or 0,
            "total_activations": total_activations or 0,
            "active_sessions": active_sessions or 0,
            "new_licenses_24h": new_24h or 0,
            "new_activations_24h": new_act_24h or 0,
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_license_by_key(self, key: str) -> License:
        result = await self._db.execute(select(License).where(License.key == key))
        lic = result.scalar_one_or_none()
        if lic is None:
            raise LicenseNotFound(f"License key not found: {key[:6]}…")
        return lic

    async def _get_license(self, license_id: uuid.UUID) -> License:
        lic = await self._db.get(License, license_id)
        if lic is None:
            raise LicenseNotFound(f"License {license_id} not found")
        return lic

    async def _get_plan(self, plan_id: uuid.UUID) -> Plan:
        plan = await self._db.get(Plan, plan_id)
        if plan is None:
            raise LicenseNotFound("Plan not found")
        return plan

    def _effective_status(self, lic: License) -> LicenseStatus:
        if lic.status == LicenseStatus.ACTIVE:
            now = datetime.now(tz=UTC)
            if lic.expires_at is not None and now > lic.expires_at:
                return LicenseStatus.EXPIRED
            if lic.suspended_until is not None and now < lic.suspended_until:
                return LicenseStatus.SUSPENDED
        return lic.status

    def _assert_license_valid(self, lic: License) -> None:
        status = self._effective_status(lic)
        if status == LicenseStatus.EXPIRED:
            raise LicenseExpired("License subscription has expired")
        if status == LicenseStatus.REVOKED:
            raise LicenseRevoked("License has been revoked")
        if status == LicenseStatus.SUSPENDED:
            raise LicenseSuspended("License is temporarily suspended")

    async def _count_active_activations(self, license_id: uuid.UUID) -> int:
        n = await self._db.scalar(
            select(func.count())
            .select_from(Activation)
            .where(Activation.license_id == license_id, Activation.is_active.is_(True))
        )
        return n or 0

    async def _find_activation(
        self, license_id: uuid.UUID, machine_fingerprint: str
    ) -> Activation | None:
        result = await self._db.execute(
            select(Activation).where(
                Activation.license_id == license_id,
                Activation.machine_fingerprint == machine_fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def _upsert_activation(
        self,
        *,
        license_id: uuid.UUID,
        machine_fingerprint: str,
        device_info: dict,
        client_version: str,
    ) -> Activation:
        now = datetime.now(tz=UTC)
        existing = await self._find_activation(license_id, machine_fingerprint)
        if existing:
            existing.is_active = True
            existing.last_seen = now
            existing.client_version = client_version
            existing.platform = device_info.get("platform", existing.platform)
            existing.os_version = device_info.get("os_version", existing.os_version)
            existing.arch = device_info.get("arch", existing.arch)
            return existing

        activation = Activation(
            license_id=license_id,
            machine_fingerprint=machine_fingerprint,
            platform=device_info.get("platform", ""),
            os_version=device_info.get("os_version", ""),
            arch=device_info.get("arch", ""),
            client_version=client_version,
            is_active=True,
            last_seen=now,
        )
        self._db.add(activation)
        await self._db.flush()
        return activation
