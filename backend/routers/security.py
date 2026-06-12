"""
AgentMax Security Configuration and Anti-Crack Router

This module provides security endpoints for the anti-crack system
and configuration management.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import structlog
from backend.core.config import get_settings
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/security", tags=["Security"])


# === Request Models ===


class SecurityCheckRequest(BaseModel):
    check_type: str = Field(..., description="Type of security check to perform")
    include_details: bool = Field(default=False, description="Include detailed results")


class AntiCrackConfigUpdate(BaseModel):
    check_interval_ms: int | None = Field(None, ge=1000, le=60000)
    max_failed_checks: int | None = Field(None, ge=1, le=10)
    auto_lock_on_detection: bool | None = None
    check_debugger: bool | None = None
    check_virtual_machine: bool | None = None
    check_memory_integrity: bool | None = None
    check_timing_anomaly: bool | None = None
    check_suspicious_modules: bool | None = None
    check_network_anomaly: bool | None = None


class LicenseActivationRequest(BaseModel):
    license_key: str = Field(..., min_length=32, max_length=256)
    device_id: str = Field(..., min_length=16)
    device_name: str | None = Field(None, max_length=128)
    hardware_signature: str | None = Field(None)


class TokenRefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=32)


# === Response Models ===


class SecurityCheckResult(BaseModel):
    check_type: str
    passed: bool
    details: dict | None = None
    timestamp: str


class AntiCrackStatus(BaseModel):
    enabled: bool
    monitoring: bool
    locked: bool
    last_check: str | None = None
    failed_checks_count: int
    recent_detections: list[dict] = Field(default_factory=list)


class DeviceRegistrationResult(BaseModel):
    device_id: str
    device_name: str | None
    activation_token: str
    expires_at: str
    max_devices: int
    current_devices: int


# === Dependencies ===


def require_security_access(request: Request) -> bool:
    """DEV-ONLY: Verify the request has appropriate security headers.

    WARNING: These endpoints return simulated/mock data. They are NOT real
    security enforcement. In production, actual anti-crack checks are handled
    by the Rust/Tauri layer, not this Python backend.
    """
    log.warning(
        "security.dev_endpoint_called",
        path=request.url.path,
        warning="This endpoint returns mock data only",
    )
    return True


# === Security Endpoints ===


@router.get("/status", response_model=AntiCrackStatus)
async def get_security_status(request: Request):
    """Get current anti-crack system status"""
    settings = get_settings()

    if not settings.enable_anti_crack:
        return AntiCrackStatus(
            enabled=False,
            monitoring=False,
            locked=False,
            failed_checks_count=0,
            recent_detections=[],
        )

    # In a real implementation, this would query the actual anti-crack system
    # For now, return a mock status
    return AntiCrackStatus(
        enabled=True,
        monitoring=True,
        locked=False,
        last_check=None,
        failed_checks_count=0,
        recent_detections=[],
    )


@router.post("/check", response_model=SecurityCheckResult)
async def perform_security_check(
    request: Request,
    body: SecurityCheckRequest,
    require_security_access: bool = Depends(require_security_access),
):
    """Perform a specific security check"""
    settings = get_settings()

    if not settings.enable_anti_crack:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Anti-crack system is disabled"
        )

    check_handlers = {
        "debugger": "check_debugger_present",
        "vm": "check_virtual_machine",
        "virtual_machine": "check_virtual_machine",
        "memory": "check_memory_integrity",
        "timing": "check_timing_anomaly",
        "modules": "check_suspicious_modules",
        "network": "check_network_interception",
        "signature": "verify_binary_signature",
        "injection": "check_code_injection",
    }

    handler_name = check_handlers.get(body.check_type.lower())

    if not handler_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid check type: {body.check_type}"
        )

    # In production, this would call the actual C++ anti-crack functions
    # For now, return a mock result
    result = SecurityCheckResult(
        check_type=body.check_type,
        passed=True,
        details={"handler": handler_name} if body.include_details else None,
        timestamp=datetime.now(UTC).isoformat(),
    )

    log.info("security_check_performed", check_type=body.check_type, passed=result.passed)

    return result


@router.get("/checks/all")
async def get_all_security_checks(
    request: Request, require_security_access: bool = Depends(require_security_access)
):
    """Run all security checks and return results"""
    settings = get_settings()

    if not settings.enable_anti_crack:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Anti-crack system is disabled"
        )

    checks = ["debugger", "vm", "memory", "timing", "modules", "network", "signature", "injection"]

    results = []
    all_passed = True

    for check_type in checks:
        # In production, each check would call actual C++ functions
        result = {
            "check_type": check_type,
            "passed": True,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        results.append(result)

        if not result["passed"]:
            all_passed = False

    return {
        "all_passed": all_passed,
        "results": results,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.post("/config")
async def update_anti_crack_config(
    request: Request,
    config: AntiCrackConfigUpdate,
    require_security_access: bool = Depends(require_security_access),
):
    """Update anti-crack system configuration"""
    settings = get_settings()

    if not settings.enable_anti_crack:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Anti-crack system is disabled"
        )

    # In production, this would update the actual C++ anti-crack config
    # For now, just acknowledge the request
    log.info("anti_crack_config_updated", config=config.model_dump(exclude_none=True))

    return {"status": "updated", "config": config.model_dump(exclude_none=True)}


@router.post("/lockdown")
async def trigger_lockdown(
    request: Request,
    reason: str = Body(..., embed=True),
    permanent: bool = Body(default=False, embed=True),
    require_security_access: bool = Depends(require_security_access),
):
    """Manually trigger a system lockdown"""
    settings = get_settings()

    if not settings.enable_anti_crack:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Anti-crack system is disabled"
        )

    # In production, this would trigger actual lockdown
    log.warning("manual_lockdown_triggered", reason=reason, permanent=permanent)

    return {"status": "locked", "reason": reason, "permanent": permanent}


@router.post("/unlock")
async def unlock_system(
    request: Request,
    admin_secret: str = Body(..., embed=True),
    require_security_access: bool = Depends(require_security_access),
):
    """Attempt to unlock a locked system"""
    settings = get_settings()

    if admin_secret != settings.admin_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin secret")

    # In production, this would unlock the actual system
    log.info("system_unlocked")

    return {"status": "unlocked"}


# === License Activation Endpoints ===


@router.post("/license/activate", response_model=DeviceRegistrationResult)
async def activate_license(request: Request, body: LicenseActivationRequest):
    """Activate a license for a new device"""
    # Verify license key format
    if not body.license_key or len(body.license_key) < 32:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid license key format"
        )

    # In production, this would:
    # 1. Verify the license with Whop
    # 2. Check device limits
    # 3. Generate activation token
    # 4. Register the device

    # Mock response for now
    return DeviceRegistrationResult(
        device_id=body.device_id,
        device_name=body.device_name,
        activation_token="mock_token_" + str(uuid4()),
        expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        max_devices=3,
        current_devices=1,
    )


@router.get("/license/devices")
async def list_licensed_devices(
    request: Request,
    license_key: str = Query(..., min_length=32),
    require_security_access: bool = Depends(require_security_access),
):
    """List all devices registered to a license"""
    # In production, query actual device registrations

    return {
        "devices": [
            {
                "device_id": "device_1",
                "device_name": "Primary PC",
                "registered_at": "2024-01-15T10:30:00Z",
                "last_seen": "2024-01-20T15:45:00Z",
                "active": True,
            }
        ]
    }


@router.delete("/license/devices/{device_id}")
async def remove_licensed_device(
    request: Request,
    device_id: str,
    license_key: str = Query(..., min_length=32),
    require_security_access: bool = Depends(require_security_access),
):
    """Remove a device from a license"""
    # In production, remove actual device registration

    log.info("device_removed", device_id=device_id, license_key=license_key[:8] + "...")

    return {"status": "removed", "device_id": device_id}


# === Token Management ===


@router.post("/token/refresh")
async def refresh_access_token(request: Request, body: TokenRefreshRequest):
    """Refresh an access token"""
    # In production, verify refresh token and issue new access token

    return {
        "access_token": "new_access_token_" + str(uuid4()),
        "token_type": "bearer",
        "expires_in": get_settings().jwt_expiration_minutes * 60,
    }


@router.post("/token/revoke")
async def revoke_token(request: Request, token: str = Body(..., embed=True)):
    """Revoke an access or refresh token"""
    # In production, add token to blacklist

    log.info("token_revoked")

    return {"status": "revoked"}


# === Hardware Security ===


@router.get("/hardware/fingerprint")
async def get_hardware_fingerprint(request: Request):
    """Get the hardware fingerprint for this device"""
    # In production, this would call the C++ hardware security module

    return {
        "cpu_id": "mock_cpu_id",
        "disk_serial": "mock_disk_serial",
        "motherboard_serial": "mock_mb_serial",
        "bios_uuid": "mock_bios_uuid",
        "machine_id": "mock_machine_id",
    }


@router.post("/hardware/verify")
async def verify_hardware(request: Request, fingerprint: dict = Body(..., embed=True)):
    """Verify hardware hasn't changed significantly"""
    # In production, compare with stored baseline

    baseline = {
        "cpu_id": "stored_cpu_id",
        "disk_serial": "stored_disk_serial",
        "motherboard_serial": "stored_mb_serial",
    }

    changes = []
    for key, value in fingerprint.items():
        if key in baseline and baseline[key] != value:
            changes.append(key)

    return {"verified": len(changes) == 0, "changed_components": changes if changes else None}


# === Rate Limiting Status ===


@router.get("/ratelimit/status")
async def get_rate_limit_status(request: Request, client_id: str | None = Query(None)):
    """Get current rate limit status for a client"""
    settings = get_settings()

    if not settings.rate_limit_enabled:
        return {"enabled": False}

    # In production, query Redis for actual rate limit status

    return {
        "enabled": True,
        "limit": settings.rate_limit_requests_per_minute,
        "remaining": settings.rate_limit_requests_per_minute - 10,
        "reset_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }


# === Security Headers ===


@router.get("/headers")
async def get_security_headers(request: Request):
    """Get current security headers configuration"""
    return {
        "content_security_policy": "default-src 'self'",
        "x_frame_options": "DENY",
        "x_content_type_options": "nosniff",
        "x_xss_protection": "1; mode=block",
        "strict_transport_security": "max-age=31536000; includeSubDomains",
        "referrer_policy": "strict-origin-when-cross-origin",
    }
