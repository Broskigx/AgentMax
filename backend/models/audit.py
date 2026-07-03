"""Audit and telemetry ORM models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from backend.models.base import Base, utcnow
from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column


class AuditSeverity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    severity: Mapped[AuditSeverity] = mapped_column(
        Enum(AuditSeverity, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=AuditSeverity.info,
    )
    license_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("licenses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    activation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("activations.id", ondelete="SET NULL"), nullable=True
    )
    machine_fingerprint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("licenses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    client_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
