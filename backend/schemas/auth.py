"""
Request / response schemas for the authentication flow.

Protocol summary
----------------
1. Client → POST /v1/auth/challenge   (license_key + machine_fingerprint)
2. Server → ChallengeResponse         (nonce, server_ephemeral_public, challenge_id)
3. Client computes ECDH shared secret + HMAC response
4. Client → POST /v1/auth/activate    (challenge_id + client_pubkey + response + device_info)
5. Server → ActivateResponse          (access_token + refresh_token + offline_token + license info)
6. Client → POST /v1/auth/refresh     (refresh_token + machine_fingerprint)
7. Server → RefreshResponse           (new access_token + rotated refresh_token)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# ── Challenge ─────────────────────────────────────────────────────────────────


class ChallengeRequest(BaseModel):
    license_key: str = Field(..., min_length=5, max_length=64)
    machine_fingerprint: str = Field(..., min_length=8, max_length=64)
    client_version: str = Field("0.0.0", max_length=32)

    @field_validator("license_key")
    @classmethod
    def _clean_key(cls, v: str) -> str:
        return v.strip().upper()


class ChallengeResponse(BaseModel):
    challenge_id: uuid.UUID
    nonce: str  # 64-char hex, 32 random bytes
    timestamp: int  # Unix seconds
    server_ephemeral_public: str  # X25519 public key, base64url
    server_signing_public: str  # Ed25519 public key, base64url (client can pin)


# ── Activation ────────────────────────────────────────────────────────────────


class DeviceInfo(BaseModel):
    platform: str = Field("", max_length=32)
    os_version: str = Field("", max_length=64)
    arch: str = Field("", max_length=16)


class ActivateRequest(BaseModel):
    challenge_id: uuid.UUID
    client_ephemeral_public: str  # X25519 client public key, base64url
    response: str  # HMAC-SHA256 hex over shared secret
    machine_fingerprint: str = Field(..., min_length=8, max_length=64)
    device_info: DeviceInfo = Field(default_factory=DeviceInfo)


class LicenseSummary(BaseModel):
    id: uuid.UUID
    key: str
    plan: str
    status: str
    expires_at: datetime | None
    max_devices: int


class ActivateResponse(BaseModel):
    access_token: str
    refresh_token: str
    offline_token: str  # Ed25519-signed, valid 24 h without internet
    token_type: str = "Bearer"
    expires_in: int  # seconds until access_token expires
    license: LicenseSummary


# ── Refresh ───────────────────────────────────────────────────────────────────


class RefreshRequest(BaseModel):
    refresh_token: str
    machine_fingerprint: str = Field(..., min_length=8, max_length=64)


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str  # rotated on every refresh
    token_type: str = "Bearer"
    expires_in: int


# ── Heartbeat ─────────────────────────────────────────────────────────────────


class HeartbeatRequest(BaseModel):
    machine_fingerprint: str = Field(..., min_length=8, max_length=64)


class HeartbeatResponse(BaseModel):
    valid: bool
    status: str  # active / expired / revoked / suspended
    plan: str
    next_heartbeat_in: int  # seconds -- client should schedule next call
    server_time: int  # Unix seconds for clock sync


# ── Logout ────────────────────────────────────────────────────────────────────


class LogoutResponse(BaseModel):
    ok: bool = True
