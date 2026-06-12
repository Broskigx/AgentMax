"""
/v1/admin -- admin management endpoints.

All routes require X-Admin-Secret header matching ADMIN_SECRET env var.
In production, sit this behind mTLS or a private VPN.

Routes
------
GET    /v1/admin/metrics                    dashboard metrics
GET    /v1/admin/licenses                   list licenses
POST   /v1/admin/licenses                   create license
GET    /v1/admin/licenses/{id}              license detail
PATCH  /v1/admin/licenses/{id}              update license
POST   /v1/admin/licenses/{id}/revoke       revoke license
GET    /v1/admin/sessions                   list active sessions
DELETE /v1/admin/sessions/{id}              revoke session
GET    /v1/admin/audit                      audit log
GET    /v1/admin/plans                      list plans
POST   /v1/admin/plans                      create plan
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.models.audit import AuditEvent
from backend.models.license import Activation, Plan
from backend.models.session import Session, SessionStatus
from backend.schemas.license import (
    AuditEventOut,
    LicenseCreate,
    LicenseOut,
    LicenseRevoke,
    LicenseUpdate,
    MetricsOut,
    PlanCreate,
    PlanOut,
    SessionOut,
)
from backend.schemas.pagination import Page
from backend.services.audit_service import AuditService
from backend.services.license_service import LicenseService
from backend.services.session_service import SessionService
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["admin"])
_settings = get_settings()


async def _admin_auth(
    x_admin_secret: Annotated[str | None, Header()] = None,
) -> None:
    """Simple shared-secret admin auth guard."""
    import hmac as _hmac

    if x_admin_secret is None or not _hmac.compare_digest(x_admin_secret, _settings.admin_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "admin_auth_required", "message": "Admin authentication required"},
        )


AdminDep = Depends(_admin_auth)


# ── Metrics ───────────────────────────────────────────────────────────────────


@router.get("/metrics", response_model=MetricsOut, dependencies=[AdminDep])
async def metrics(db: AsyncSession = Depends(get_db)) -> MetricsOut:
    svc = LicenseService(db)
    return MetricsOut(**(await svc.get_metrics()))


@router.get("/stats/timeline", dependencies=[AdminDep])
async def stats_timeline(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)

    act_rows = await db.execute(
        select(
            func.date(Activation.created_at).label("day"),
            func.count().label("count"),
        )
        .where(Activation.created_at >= cutoff)
        .group_by(func.date(Activation.created_at))
        .order_by(func.date(Activation.created_at))
    )
    act_by_day = {row.day: row.count for row in act_rows}

    ses_rows = await db.execute(
        select(
            func.date(Session.created_at).label("day"),
            func.count().label("count"),
        )
        .where(Session.created_at >= cutoff)
        .group_by(func.date(Session.created_at))
        .order_by(func.date(Session.created_at))
    )
    ses_by_day = {row.day: row.count for row in ses_rows}

    today = datetime.now(tz=UTC).date()
    return [
        {
            "date": f"{(today - timedelta(days=days - 1 - i)).strftime('%b')} {(today - timedelta(days=days - 1 - i)).day}",
            "activations": act_by_day.get(today - timedelta(days=days - 1 - i), 0),
            "sessions": ses_by_day.get(today - timedelta(days=days - 1 - i), 0),
        }
        for i in range(days)
    ]


# ── Plans ─────────────────────────────────────────────────────────────────────


@router.get("/plans", response_model=list[PlanOut], dependencies=[AdminDep])
async def list_plans(db: AsyncSession = Depends(get_db)) -> list[PlanOut]:
    result = await db.execute(select(Plan).order_by(Plan.created_at))
    plans = list(result.scalars().all())
    return [PlanOut.model_validate(p, from_attributes=True) for p in plans]


@router.post("/plans", response_model=PlanOut, status_code=201, dependencies=[AdminDep])
async def create_plan(
    body: PlanCreate,
    db: AsyncSession = Depends(get_db),
) -> PlanOut:
    plan = Plan(
        name=body.name,
        display_name=body.display_name,
        max_devices=body.max_devices,
        duration_days=body.duration_days,
        is_active=body.is_active,
        metadata_=body.metadata,
    )
    db.add(plan)
    await db.flush()
    return PlanOut.model_validate(plan, from_attributes=True)


# ── Licenses ──────────────────────────────────────────────────────────────────


@router.get("/licenses", dependencies=[AdminDep])
async def list_licenses(
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> Page:
    svc = LicenseService(db)
    licenses, total = await svc.list_licenses(status=status, q=q, limit=limit, offset=offset)
    return Page.of([_serialize_license(lic) for lic in licenses], total, limit, offset)


@router.post("/licenses", response_model=LicenseOut, status_code=201, dependencies=[AdminDep])
async def create_license(
    body: LicenseCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    svc = LicenseService(db)
    lic = await svc.create_license(
        plan_id=body.plan_id,
        user_email=str(body.user_email) if body.user_email else None,
        user_name=body.user_name,
        notes=body.notes,
        metadata_=body.metadata,
    )
    return _serialize_license(lic)


@router.patch("/licenses/{license_id}", dependencies=[AdminDep])
async def update_license(
    license_id: uuid.UUID,
    body: LicenseUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from datetime import datetime

    from backend.models.license import License
    from sqlalchemy import update as sql_update

    updates = body.model_dump(exclude_none=True)
    if "metadata" in updates:
        updates["metadata_"] = updates.pop("metadata")
    updates["updated_at"] = datetime.now(tz=UTC)
    await db.execute(sql_update(License).where(License.id == license_id).values(**updates))
    return {"ok": True}


@router.post("/licenses/{license_id}/revoke", dependencies=[AdminDep])
async def revoke_license(
    license_id: uuid.UUID,
    body: LicenseRevoke,
    db: AsyncSession = Depends(get_db),
) -> dict:
    svc = LicenseService(db)
    await svc.revoke_license(license_id, reason=body.reason)
    return {"ok": True}


# ── Sessions ──────────────────────────────────────────────────────────────────


@router.get("/sessions", response_model=list[SessionOut], dependencies=[AdminDep])
async def list_sessions(
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[SessionOut]:
    result = await db.execute(
        select(Session)
        .where(Session.status == SessionStatus.ACTIVE)
        .order_by(Session.last_heartbeat.desc())
        .limit(limit)
    )
    sessions = list(result.scalars().all())
    return [SessionOut.model_validate(s, from_attributes=True) for s in sessions]


@router.delete("/sessions/{session_id}", dependencies=[AdminDep])
async def revoke_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    svc = SessionService(db)
    await svc.invalidate(session_id, reason="admin_revocation")
    audit = AuditService(db)
    await audit.log(
        event_type="admin.session_revoked",
        session_id=session_id,
        message=f"Session {session_id} revoked by admin",
        severity="warning",
    )
    return {"ok": True}


# ── Audit log ─────────────────────────────────────────────────────────────────


@router.get("/audit", response_model=list[AuditEventOut], dependencies=[AdminDep])
async def audit_log(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    event_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[AuditEventOut]:
    q = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)
    if event_type:
        q = q.where(AuditEvent.event_type == event_type)
    result = await db.execute(q)
    events = list(result.scalars().all())
    return [AuditEventOut.model_validate(e, from_attributes=True) for e in events]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialize_license(lic) -> dict:
    return {
        "id": lic.id,
        "key": lic.key,
        "plan_id": lic.plan_id,
        "plan_name": lic.plan.name if hasattr(lic, "plan") and lic.plan else "",
        "status": lic.status.value,
        "expires_at": lic.expires_at,
        "user_email": lic.user_email,
        "user_name": lic.user_name,
        "notes": lic.notes,
        "metadata": lic.metadata_,
        "created_at": lic.created_at,
        "updated_at": lic.updated_at,
        "activations": [],
        "active_sessions": [],
    }
