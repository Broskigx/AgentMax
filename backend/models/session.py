"""Session ORM model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from backend.models.base import Base
from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from backend.models.license import Activation, License


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    LOGGED_OUT = "logged_out"
    EXPIRED = "expired"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("licenses.id"), nullable=False, index=True)
    activation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("activations.id"), nullable=False, index=True)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=SessionStatus.ACTIVE,
        index=True,
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    access_token_jti: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    machine_fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    plan: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    license: Mapped[License] = relationship("License", foreign_keys=[license_id], lazy="noload")
    activation: Mapped[Activation] = relationship("Activation", foreign_keys=[activation_id], lazy="noload")
