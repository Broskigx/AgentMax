"""
Whop Webhook Router - Handles subscription events from Whop

This router processes webhooks from Whop for:
- Payment events (succeeded, failed)
- Subscription events (created, updated, cancelled, expired)
- Membership events (created, updated, banned)
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC

import orjson
import structlog
from backend.core.config import get_settings
from backend.core.exceptions import AgentMaxError
from backend.services.whop_integration import (
    WhopAPIConfig,
    WhopLicenseService,
    WhopPlanConfig,
    WhopProductType,
    WhopWebhookEvent,
    WhopWebhookPayload,
    WhopWebhookService,
)
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks/whop", tags=["Whop"])


class WhopWebhookResponse(BaseModel):
    """Response for webhook processing"""

    received: bool
    event_id: str | None = None
    event_type: str | None = None
    processed: bool = False
    error: str | None = None


class WhopWebhookError(AgentMaxError):
    """Webhook processing error"""

    def __init__(self, message: str, event_id: str | None = None):
        super().__init__(
            message=message,
            error_code="whop_webhook_error",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
        self.event_id = event_id


def get_whop_config() -> WhopAPIConfig:
    """Get Whop configuration from settings"""
    settings = get_settings()

    if not settings.whop_api_key:
        raise WhopWebhookError("Whop not configured")

    return WhopAPIConfig(
        api_key=settings.whop_api_key,
        webhook_secret=settings.whop_webhook_secret,
        app_id=settings.whop_app_id,
        base_url=settings.whop_api_url or "https://api.whop.com",
        verify_webhook_signature=settings.whop_verify_signature,
    )


async def get_license_service() -> WhopLicenseService:
    """Dependency to get Whop license service"""
    config = get_whop_config()
    return WhopLicenseService(config)


def verify_whop_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify webhook signature from Whop"""
    if not signature:
        return False

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature)


@router.post("/events", response_model=WhopWebhookResponse)
async def handle_whop_webhook(
    request: Request,
    x_whop_signature: str | None = Header(None, alias="X-Whop-Signature"),
    x_whop_event_id: str | None = Header(None, alias="X-Whop-Event-Id"),
):
    """
    Main webhook endpoint for all Whop events.

    Validates signature, processes event, and updates license accordingly.
    """
    settings = get_settings()
    payload = await request.body()

    if settings.whop_verify_signature and x_whop_signature:
        if not verify_whop_signature(payload, x_whop_signature, settings.whop_webhook_secret):
            log.warning(
                "whop_invalid_signature",
                signature=x_whop_signature[:20] if x_whop_signature else None,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature"
            )

    try:
        data = orjson.loads(payload)
    except orjson.JSONDecodeError as exc:
        log.error("whop_invalid_json", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload"
        ) from exc

    try:
        webhook_payload = WhopWebhookPayload(**data)
    except Exception as exc:
        log.error("whop_parse_error", error=str(exc), data=data)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook payload"
        ) from exc

    log.info(
        "whop_webhook_received",
        event_id=webhook_payload.event_id,
        event_type=webhook_payload.event_type.value,
        member_id=webhook_payload.member_id,
        product_type=webhook_payload.product_type.value if webhook_payload.product_type else None,
    )

    try:
        whop_config = get_whop_config()
        license_service = WhopLicenseService(whop_config)
        webhook_service = WhopWebhookService(
            webhook_secret=settings.whop_webhook_secret,
            license_service=license_service,
            verify_signature=False,
        )

        result = await webhook_service.dispatch_event(webhook_payload)

        log.info(
            "whop_webhook_processed",
            event_id=webhook_payload.event_id,
            event_type=webhook_payload.event_type.value,
            result=result,
        )

        return WhopWebhookResponse(
            received=True,
            event_id=webhook_payload.event_id,
            event_type=webhook_payload.event_type.value,
            processed=True,
        )

    except Exception as exc:
        log.error("whop_webhook_error", event_id=webhook_payload.event_id, error=str(exc))

        return WhopWebhookResponse(
            received=True,
            event_id=webhook_payload.event_id,
            event_type=webhook_payload.event_type.value,
            processed=False,
            error=str(exc),
        )


@router.get("/test/{event_type}")
async def test_webhook_event(event_type: str):
    """Test endpoint to trigger webhook processing"""

    event = WhopWebhookEvent(event_type)

    test_payload = WhopWebhookPayload(
        event_id=f"test_{int(time.time())}",
        event_type=event,
        timestamp=datetime.now(UTC),
        product_id="test_product",
        product_type=WhopProductType.PRO,
        member_id="test_member_123",
        member_email="test@example.com",
        membership_id="test_membership_456",
        status="active",
        started_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )

    return {"test_event": event_type, "payload": test_payload.model_dump()}


@router.post("/verify-license")
async def verify_license_from_whop(
    license_key: str = Body(..., embed=True),
    current_service: WhopLicenseService = Depends(get_license_service),
):
    """Verify a license directly from Whop"""

    license = await current_service.verify_license(license_key)

    if not license:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="License not found or invalid"
        )

    return {
        "valid": True,
        "license_id": license.whop_license_id,
        "product_type": license.product_type.value,
        "status": license.status,
        "expires_at": license.expires_at.isoformat() if license.expires_at else None,
    }


@router.get("/pricing")
async def get_pricing_plans():
    """Get available pricing plans"""
    from backend.services.whop_integration import WhopPricingDisplay

    return {
        "plans": WhopPricingDisplay.get_all_pricing(),
        "currency": "USD",
        "billing_period": "monthly",
    }


@router.get("/pricing/{plan_type}")
async def get_plan_details(plan_type: WhopProductType):
    """Get specific plan details"""
    from backend.services.whop_integration import WhopPricingDisplay

    return WhopPricingDisplay.get_pricing_card(plan_type)


class WhopSubscriptionStatus(BaseModel):
    """Current subscription status for a member"""

    member_id: str
    is_active: bool
    plan_type: WhopProductType | None = None
    daily_tokens: int = 0
    max_devices: int = 0
    expires_at: datetime | None = None
    features: list[str] = Field(default_factory=list)


@router.get("/subscription/{member_id}", response_model=WhopSubscriptionStatus)
async def get_member_subscription(
    member_id: str, license_service: WhopLicenseService = Depends(get_license_service)
):
    """Get subscription status for a specific member"""

    memberships_response = await license_service.session.get(
        f"/v1/members/{member_id}/memberships", params={"app_id": license_service.config.app_id}
    )

    if memberships_response.status_code != 200:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    memberships = memberships_response.json().get("memberships", [])

    if not memberships:
        return WhopSubscriptionStatus(member_id=member_id, is_active=False)

    active_membership = None
    for membership in memberships:
        if membership.get("status") in ["active", "trialing"]:
            active_membership = membership
            break

    if not active_membership:
        return WhopSubscriptionStatus(member_id=member_id, is_active=False)

    plan_type = WhopProductType(active_membership.get("plan_type", "starter"))
    plan_config = WhopPlanConfig.get_config(plan_type)

    return WhopSubscriptionStatus(
        member_id=member_id,
        is_active=True,
        plan_type=plan_type,
        daily_tokens=plan_config["daily_tokens"],
        max_devices=plan_config["max_devices"],
        expires_at=active_membership.get("expires_at"),
        features=plan_config["features"],
    )


@router.post("/check-access")
async def check_product_access(
    member_id: str = Body(..., embed=True),
    product_type: WhopProductType = Body(..., embed=True),
    license_service: WhopLicenseService = Depends(get_license_service),
):
    """Check if member has access to a specific product"""

    has_access = await license_service.check_product_access(member_id, product_type)

    plan_config = WhopPlanConfig.get_config(product_type)

    return {
        "member_id": member_id,
        "product_type": product_type.value,
        "has_access": has_access,
        "daily_tokens": plan_config["daily_tokens"],
        "max_devices": plan_config["max_devices"],
    }


from datetime import datetime, timedelta
