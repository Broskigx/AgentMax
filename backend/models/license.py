"""License, Plan, and Activation ORM models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from backend.models.base import Base
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship


class LicenseStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUSPENDED = "suspended"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    max_devices: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    licenses: Mapped[list[License]] = relationship("License", back_populates="plan", lazy="noload")


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("plans.id"), nullable=False)
    status: Mapped[LicenseStatus] = mapped_column(
        Enum(LicenseStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=LicenseStatus.ACTIVE,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    user_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    plan: Mapped[Plan] = relationship("Plan", back_populates="licenses", lazy="noload")
    activations: Mapped[list[Activation]] = relationship(
        "Activation", back_populates="license", lazy="noload"
    )


class Activation(Base):
    __tablename__ = "activations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    license_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("licenses.id"), nullable=False, index=True
    )
    machine_fingerprint: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    os_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    arch: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    client_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    license: Mapped[License] = relationship("License", back_populates="activations", lazy="noload")
