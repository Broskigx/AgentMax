"""Admin-facing license management schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field

ORM_MODEL_CONFIG = ConfigDict(from_attributes=True)
# Allow validating ORM objects whose JSON column is mapped to the `metadata_`
# attribute (SQLAlchemy reserves the plain `metadata` name for the registry)
# while still accepting/serializing the field as `metadata`.
ORM_MODEL_CONFIG_META = ConfigDict(from_attributes=True, populate_by_name=True)
_METADATA_ALIAS = AliasChoices("metadata_", "metadata")


# ── Plan schemas ──────────────────────────────────────────────────────────────


class PlanCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    display_name: str = Field(..., min_length=2, max_length=128)
    max_devices: int = Field(1, ge=1, le=100)
    duration_days: int | None = Field(None, ge=1, description="None = lifetime")
    is_active: bool = True
    metadata: dict = Field(default_factory=dict)


class PlanOut(PlanCreate):
    model_config = ORM_MODEL_CONFIG_META

    metadata: dict = Field(default_factory=dict, validation_alias=_METADATA_ALIAS)
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ── License schemas ───────────────────────────────────────────────────────────


class LicenseCreate(BaseModel):
    plan_id: uuid.UUID
    user_email: EmailStr | None = None
    user_name: str | None = Field(None, max_length=256)
    notes: str | None = None
    metadata: dict = Field(default_factory=dict)


class LicenseUpdate(BaseModel):
    user_email: EmailStr | None = None
    user_name: str | None = None
    notes: str | None = None
    metadata: dict | None = None


class LicenseRevoke(BaseModel):
    reason: str = Field(..., min_length=4, max_length=512)


class ActivationOut(BaseModel):
    model_config = ORM_MODEL_CONFIG

    id: uuid.UUID
    machine_fingerprint: str
    platform: str
    os_version: str
    arch: str
    client_version: str
    is_active: bool
    last_seen: datetime | None
    created_at: datetime


class SessionOut(BaseModel):
    model_config = ORM_MODEL_CONFIG

    id: uuid.UUID
    status: str
    machine_fingerprint: str
    plan: str
    ip_address: str | None
    last_heartbeat: datetime | None
    expires_at: datetime
    created_at: datetime


class LicenseOut(BaseModel):
    model_config = ORM_MODEL_CONFIG

    id: uuid.UUID
    key: str
    plan_id: uuid.UUID
    plan_name: str
    status: str
    expires_at: datetime | None
    user_email: str | None
    user_name: str | None
    notes: str | None
    metadata: dict
    created_at: datetime
    updated_at: datetime
    activations: list[ActivationOut] = Field(default_factory=list)
    active_sessions: list[SessionOut] = Field(default_factory=list)


# ── Admin metrics ─────────────────────────────────────────────────────────────


class MetricsOut(BaseModel):
    total_licenses: int
    active_licenses: int
    expired_licenses: int
    revoked_licenses: int
    total_activations: int
    active_sessions: int
    new_licenses_24h: int
    new_activations_24h: int


# ── Audit log ─────────────────────────────────────────────────────────────────


class AuditEventOut(BaseModel):
    model_config = ORM_MODEL_CONFIG

    id: uuid.UUID
    created_at: datetime
    event_type: str
    severity: str
    license_id: uuid.UUID | None
    session_id: uuid.UUID | None
    machine_fingerprint: str | None
    ip_address: str | None
    message: str
    payload: dict


# ── Telemetry ─────────────────────────────────────────────────────────────────


class TelemetryEventIn(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "event_name": "task.completed",
                "client_version": "1.2.0",
                "platform": "win32",
                "payload": {"duration_ms": 4200, "task_type": "screen_read"},
            }
        }
    )

    event_name: str = Field(..., max_length=64, pattern=r"^[a-z][a-z0-9_.]+$")
    client_version: str = Field("", max_length=32)
    platform: str = Field("", max_length=32)
    payload: dict = Field(default_factory=dict)


class TelemetryBatchIn(BaseModel):
    events: list[TelemetryEventIn] = Field(..., min_length=1, max_length=100)


# ── Update manifest ───────────────────────────────────────────────────────────


class UpdateChannelEnum(str):
    STABLE = "stable"
    BETA = "beta"


class UpdateManifest(BaseModel):
    channel: str
    version: str
    min_version: str
    release_notes: str
    download_url: str
    sha256: str
    signature: str  # Ed25519 over sha256 hex, base64url
    published_at: datetime
    mandatory: bool = False
