"""Append-only audit logging service."""

from __future__ import annotations

import uuid
from typing import Any, Literal

import structlog
from backend.models.audit import AuditEvent, AuditSeverity, TelemetryEvent
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

SeverityT = Literal["info", "warning", "critical"]


class AuditService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def log(
        self,
        *,
        event_type: str,
        message: str,
        license_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        activation_id: uuid.UUID | None = None,
        machine_fingerprint: str | None = None,
        ip_address: str | None = None,
        request_id: str | None = None,
        severity: SeverityT = "info",
        payload: dict[str, Any] | None = None,
    ) -> None:
        import structlog.contextvars as ctx

        rid = request_id or ctx.get_contextvars().get("request_id")
        event = AuditEvent(
            event_type=event_type,
            severity=AuditSeverity(severity),
            license_id=license_id,
            session_id=session_id,
            activation_id=activation_id,
            machine_fingerprint=machine_fingerprint,
            ip_address=ip_address,
            request_id=rid,
            message=message,
            payload=payload or {},
        )
        self._db.add(event)
        # Flush immediately so the event is persisted even if the parent tx rolls back.
        await self._db.flush([event])

        log.bind(
            event_type=event_type,
            severity=severity,
            license_id=str(license_id) if license_id else None,
        ).info("audit.event", message=message)

    async def ingest_telemetry(
        self,
        *,
        license_id: uuid.UUID | None,
        events: list[dict[str, Any]],
    ) -> int:
        """Batch-insert telemetry events. Returns count inserted."""
        rows = []
        for ev in events:
            row = TelemetryEvent(
                license_id=license_id,
                event_name=ev.get("event_name", "unknown"),
                client_version=ev.get("client_version", ""),
                platform=ev.get("platform", ""),
                payload=ev.get("payload", {}),
            )
            rows.append(row)
        self._db.add_all(rows)
        await self._db.flush(rows)
        return len(rows)
