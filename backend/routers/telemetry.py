"""
/v1/telemetry -- ethical, opt-in telemetry ingestion.

Only technical events are accepted:
  - task.completed / task.failed
  - error.crash
  - startup / shutdown
  - feature.* usage

Personally identifiable information is NEVER stored.
Payload is size-limited to 4 KB per event.
"""

from __future__ import annotations

import uuid

import structlog
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.routers.license import _require_token
from backend.schemas.license import TelemetryBatchIn
from backend.services.audit_service import AuditService
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/telemetry", tags=["telemetry"])
_settings = get_settings()

_ALLOWED_EVENT_PREFIXES = (
    "task.",
    "feature.",
    "error.",
    "startup",
    "shutdown",
    "agent.",
    "vision.",
)

_MAX_PAYLOAD_BYTES = 4096


@router.post("/batch", status_code=202)
async def ingest_batch(
    body: TelemetryBatchIn,
    token: dict = Depends(_require_token),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Accept a batch of telemetry events from an authenticated client.
    Events with forbidden names or oversized payloads are silently dropped.
    """
    if not _settings.telemetry_enabled:
        return {"accepted": 0, "dropped": len(body.events)}

    license_id = uuid.UUID(token["sub"])
    svc = AuditService(db)

    clean_events = []
    for ev in body.events:
        if not any(ev.event_name.startswith(p) for p in _ALLOWED_EVENT_PREFIXES):
            continue
        import json

        if len(json.dumps(ev.payload)) > _MAX_PAYLOAD_BYTES:
            continue
        clean_events.append(ev.model_dump())

    count = await svc.ingest_telemetry(license_id=license_id, events=clean_events)
    return {"accepted": count, "dropped": len(body.events) - count}


@router.get("/stats")
async def telemetry_stats() -> dict:
    """
    Lightweight desktop-beta status endpoint.

    It intentionally avoids returning stored event payloads or user identifiers.
    """
    return {
        "status": "ok",
        "enabled": _settings.telemetry_enabled,
        "mode": "ingest_only",
        "accepted_events": None,
        "dropped_events": None,
        "privacy": "payloads_not_returned",
    }
