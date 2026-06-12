from __future__ import annotations

import asyncio
import enum
import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import orjson
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import select

if TYPE_CHECKING:
    pass
from backend.models.license import LicenseStatus


class WhopProductType(str, enum.Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ELITE = "elite"


class WhopPlanConfig:
    """Whop plan configurations"""

    PLANS = {
        WhopProductType.FREE: {
            "name": "Free Trial",
            "daily_tokens": 100,
            "max_devices": 1,
            "price_usd": 0.0,
            "trial_days": 7,
            "features": [
                "100 tokens/día",
                "Prueba gratis por 7 días",
                "Automatización básica",
                "1 dispositivo",
                "🟣 AgentMax Bot (limitado)",
            ],
            "includes_AGENTMAX_bot": True,
            "AGENTMAX_bot_version": "trial",
        },
        WhopProductType.STARTER: {
            "name": "Starter",
            "daily_tokens": 1000,
            "max_devices": 2,
            "price_usd": 9.99,
            "features": [
                "1000 tokens/día",
                "Automatización básica",
                "Soporte por email",
                "2 dispositivos",
            ],
            "includes_AGENTMAX_bot": False,
            "AGENTMAX_bot_version": None,
        },
        WhopProductType.PRO: {
            "name": "Pro",
            "daily_tokens": 5000,
            "max_devices": 5,
            "price_usd": 19.99,
            "refill_hours": 4,
            "features": [
                "5000 tokens/4h",
                "Suite completa de automatización",
                "Soporte prioritario",
                "5 dispositivos",
                "Modo Antiloop mejorado",
                "Acceso a todos los modelos",
                "Acceso API completo",
                "🟣 AgentMax Bot (full)",
                "🟣 Fine-tuning personalizado",
                "🟣 Análisis de datos",
                "🟣 Comprensión de imágenes",
            ],
            "includes_AGENTMAX_bot": True,
            "AGENTMAX_bot_version": "full",
        },
        WhopProductType.ELITE: {
            "name": "Elite",
            "daily_tokens": 7000,
            "max_devices": -1,  # Unlimited
            "price_usd": 79.99,
            "refill_hours": 4,
            "features": [
                "7000 tokens/4h",
                "Automatización ilimitada",
                "Soporte 24/7 premium",
                "Dispositivos ilimitados",
                "Antiloop máximo",
                "Todos los modelos",
                "Acceso API completo",
                "Integraciones custom",
                "Opciones white-label",
                "🟣 AgentMax Bot (elite)",
                "🟣 Custom AI training",
                "🟣 Priority inference",
                "🟣 dedicated support",
            ],
            "includes_AGENTMAX_bot": True,
            "AGENTMAX_bot_version": "elite",
        },
    }

    @classmethod
    def get_config(cls, plan_type: WhopProductType) -> dict:
        return cls.PLANS.get(plan_type, cls.PLANS[WhopProductType.STARTER])

    @classmethod
    def get_daily_limit(cls, plan_type: WhopProductType) -> int:
        return cls.PLANS.get(plan_type, cls.PLANS[WhopProductType.STARTER])["daily_tokens"]

    @classmethod
    def get_max_devices(cls, plan_type: WhopProductType) -> int:
        return cls.PLANS.get(plan_type, cls.PLANS[WhopProductType.STARTER])["max_devices"]

    @classmethod
    def get_price(cls, plan_type: WhopProductType) -> float:
        return cls.PLANS.get(plan_type, cls.PLANS[WhopProductType.STARTER])["price_usd"]


class WhopWebhookEvent(str, enum.Enum):
    PAYMENT_SUCCEEDED = "payment_succeeded"
    PAYMENT_FAILED = "payment_failed"
    SUBSCRIPTION_CREATED = "subscription_created"
    SUBSCRIPTION_UPDATED = "subscription_updated"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    REFUND_PROCESSED = "refund_processed"
    LICENSE_CREATED = "license_created"
    LICENSE_UPDATED = "license_updated"
    MEMBERSHIP_CREATED = "membership_created"
    MEMBERSHIP_UPDATED = "membership_updated"
    MEMBER_BANNED = "member_banned"


class WhopWebhookPayload(BaseModel):
    """Payload from Whop webhooks"""

    event_id: str = Field(..., description="Unique event ID from Whop")
    event_type: WhopWebhookEvent = Field(..., description="Type of webhook event")
    timestamp: datetime = Field(..., description="Event timestamp")
    product_id: str = Field(..., description="Whop product ID")
    product_type: WhopProductType | None = Field(None, description="Product tier type")
    member_id: str = Field(..., description="Whop member ID")
    member_email: str = Field(..., description="Member email")
    membership_id: str | None = Field(None, description="Whop membership ID")
    license_id: str | None = Field(None, description="Associated license ID")
    plan_type: WhopProductType | None = Field(None, description="Plan tier")
    status: str | None = Field(None, description="Current status")
    started_at: datetime | None = Field(None, description="Subscription start")
    expires_at: datetime | None = Field(None, description="Subscription expiration")
    cancelled_at: datetime | None = Field(None, description="Cancellation timestamp")
    metadata: dict | None = Field(default_factory=dict, description="Additional metadata")

    @field_validator("product_type", "plan_type", mode="before")
    @classmethod
    def parse_product_type(cls, v):
        if v is None:
            return None
        if isinstance(v, WhopProductType):
            return v
        if isinstance(v, str):
            return WhopProductType(v.lower())
        return None


class WhopLicense(BaseModel):
    """License information from Whop"""

    whop_license_id: str
    whop_member_id: str
    product_id: str
    product_type: WhopProductType
    status: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    metadata: dict = Field(default_factory=dict)


class WhopMember(BaseModel):
    """Member information from Whop"""

    whop_member_id: str
    email: str
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    avatar_url: HttpUrl | None = None
    is_banned: bool = False
    created_at: datetime
    updated_at: datetime


class WhopSubscription(BaseModel):
    """Subscription details from Whop"""

    whop_subscription_id: str
    whop_membership_id: str
    member_id: str
    product_id: str
    product_type: WhopProductType
    status: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool = False
    created_at: datetime
    updated_at: datetime


class WhopPayment(BaseModel):
    """Payment information from Whop"""

    whop_payment_id: str
    amount: float
    currency: str
    status: str
    member_id: str
    product_id: str
    created_at: datetime
    metadata: dict = Field(default_factory=dict)


class WhopAPIConfig(BaseModel):
    """Configuration for Whop API integration"""

    api_key: str = Field(..., description="Whop API key")
    webhook_secret: str = Field(..., description="Whop webhook secret")
    app_id: str = Field(..., description="Whop app ID")
    base_url: HttpUrl = Field(default="https://api.whop.com")
    verify_webhook_signature: bool = Field(default=True)
    retry_failed_webhooks: bool = Field(default=True)
    max_retry_attempts: int = Field(default=3)


class WhopLicenseService:
    """Service for managing Whop license integrations"""

    def __init__(self, config: WhopAPIConfig):
        self.config = config
        self.session = httpx.AsyncClient(
            base_url=str(config.base_url),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def verify_license(self, license_key: str) -> WhopLicense | None:
        """Verify a Whop license key"""
        try:
            response = await self.session.get(
                f"/v1/licenses/{license_key}", params={"app_id": self.config.app_id}
            )

            if response.status_code == 200:
                data = response.json()
                return WhopLicense(**data)
            return None
        except Exception:
            return None

    async def get_member(self, member_id: str) -> WhopMember | None:
        """Get member information from Whop"""
        try:
            response = await self.session.get(
                f"/v1/members/{member_id}", params={"app_id": self.config.app_id}
            )

            if response.status_code == 200:
                data = response.json()
                return WhopMember(**data)
            return None
        except Exception:
            return None

    async def get_subscription(self, membership_id: str) -> WhopSubscription | None:
        """Get subscription details from Whop"""
        try:
            response = await self.session.get(
                f"/v1/subscriptions/{membership_id}", params={"app_id": self.config.app_id}
            )

            if response.status_code == 200:
                data = response.json()
                return WhopSubscription(**data)
            return None
        except Exception:
            return None

    async def check_product_access(self, member_id: str, product_type: WhopProductType) -> bool:
        """Check if member has access to a specific product tier"""
        try:
            response = await self.session.get(
                f"/v1/members/{member_id}/products", params={"app_id": self.config.app_id}
            )

            if response.status_code == 200:
                products = response.json().get("products", [])

                for product in products:
                    if product.get("product_type") == product_type.value:
                        if product.get("status") in ["active", "trialing"]:
                            return True
            return False
        except Exception:
            return False

    async def create_license(
        self, member_id: str, product_type: WhopProductType, metadata: dict | None = None
    ) -> WhopLicense | None:
        """Create a new license for a member"""
        try:
            plan_config = WhopPlanConfig.get_config(product_type)

            response = await self.session.post(
                "/v1/licenses",
                json={
                    "app_id": self.config.app_id,
                    "member_id": member_id,
                    "product_id": plan_config.get("product_id", ""),
                    "metadata": metadata or {},
                },
            )

            if response.status_code in [200, 201]:
                data = response.json()
                return WhopLicense(**data)
            return None
        except Exception:
            return None

    async def revoke_license(self, license_id: str) -> bool:
        """Revoke a license"""
        try:
            response = await self.session.delete(f"/v1/licenses/{license_id}")
            return response.status_code in [200, 204]
        except Exception:
            return False


class WhopWebhookService:
    """Service for handling Whop webhooks"""

    def __init__(
        self,
        webhook_secret: str,
        license_service: WhopLicenseService,
        verify_signature: bool = True,
    ):
        self.webhook_secret = webhook_secret
        self.license_service = license_service
        self._verify_signature_enabled = verify_signature
        self._webhook_queue: asyncio.Queue = asyncio.Queue()

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify webhook signature from Whop"""
        if not self._verify_signature_enabled:
            return True

        expected = hmac.new(self.webhook_secret.encode(), payload, hashlib.sha256).hexdigest()

        return hmac.compare_digest(expected, signature)

    async def process_webhook(
        self, payload: bytes, signature: str | None = None
    ) -> WhopWebhookPayload | None:
        """Process incoming webhook from Whop"""
        if signature and not self.verify_signature(payload, signature):
            return None

        try:
            data = orjson.loads(payload)
            return WhopWebhookPayload(**data)
        except Exception:
            return None

    async def handle_payment_succeeded(self, payload: WhopWebhookPayload) -> dict:
        """Handle successful payment webhook"""
        return {
            "status": "processed",
            "event": "payment_succeeded",
            "member_id": payload.member_id,
            "product_type": payload.product_type,
        }

    async def handle_subscription_created(self, payload: WhopWebhookPayload) -> dict:
        """Handle new subscription created"""
        if not payload.plan_type:
            return {"status": "skipped", "reason": "no_plan_type"}

        license = await self.license_service.create_license(
            member_id=payload.member_id,
            product_type=payload.plan_type,
            metadata={
                "whop_membership_id": payload.membership_id,
                "whop_event_id": payload.event_id,
            },
        )

        return {
            "status": "processed",
            "event": "subscription_created",
            "license_id": license.whop_license_id if license else None,
            "plan_type": payload.plan_type.value,
        }

    async def handle_subscription_cancelled(self, payload: WhopWebhookPayload) -> dict:
        """Handle subscription cancellation"""
        if payload.license_id:
            await self.license_service.revoke_license(payload.license_id)

        return {
            "status": "processed",
            "event": "subscription_cancelled",
            "license_revoked": payload.license_id is not None,
        }

    async def handle_member_banned(self, payload: WhopWebhookPayload) -> dict:
        """Handle member banned event"""
        if payload.license_id:
            await self.license_service.revoke_license(payload.license_id)

        return {
            "status": "processed",
            "event": "member_banned",
            "license_revoked": payload.license_id is not None,
        }

    async def dispatch_event(self, payload: WhopWebhookPayload) -> dict:
        """Dispatch webhook event to appropriate handler"""
        handlers = {
            WhopWebhookEvent.PAYMENT_SUCCEEDED: self.handle_payment_succeeded,
            WhopWebhookEvent.PAYMENT_FAILED: lambda p: {
                "status": "processed",
                "event": "payment_failed",
            },
            WhopWebhookEvent.SUBSCRIPTION_CREATED: self.handle_subscription_created,
            WhopWebhookEvent.SUBSCRIPTION_UPDATED: lambda p: {
                "status": "processed",
                "event": "subscription_updated",
            },
            WhopWebhookEvent.SUBSCRIPTION_CANCELLED: self.handle_subscription_cancelled,
            WhopWebhookEvent.SUBSCRIPTION_EXPIRED: self.handle_subscription_cancelled,
            WhopWebhookEvent.REFUND_PROCESSED: self.handle_subscription_cancelled,
            WhopWebhookEvent.MEMBER_BANNED: self.handle_member_banned,
        }

        handler = handlers.get(payload.event_type)
        if handler:
            return await handler(payload)

        return {"status": "unhandled", "event_type": payload.event_type.value}


class WhopSyncService:
    """Service for syncing with Whop periodically"""

    def __init__(
        self,
        license_service: WhopLicenseService,
        db_session_factory,
        check_interval_minutes: int = 15,
    ):
        self.license_service = license_service
        self.db_session_factory = db_session_factory
        self.check_interval = timedelta(minutes=check_interval_minutes)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start the sync service"""
        self._running = True
        self._task = asyncio.create_task(self._sync_loop())

    async def stop(self):
        """Stop the sync service"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _sync_loop(self):
        """Periodic sync loop"""
        while self._running:
            try:
                await self._sync_expired_subscriptions()
                await self._sync_active_subscriptions()
            except Exception as exc:
                logging.error(f"Whop sync error: {exc}")

            await asyncio.sleep(self.check_interval.total_seconds())

    async def _sync_expired_subscriptions(self):
        """Sync and mark expired subscriptions"""
        async with self.db_session_factory() as db:
            from backend.models.license import License

            expired = await db.execute(
                select(License).where(
                    License.status == LicenseStatus.ACTIVE, License.expires_at < datetime.now(UTC)
                )
            )

            for license in expired.scalars():
                if license.whop_membership_id:
                    sub = await self.license_service.get_subscription(license.whop_membership_id)
                    if sub and sub.status not in ["active", "trialing"]:
                        license.status = LicenseStatus.EXPIRED
                        license.invalidated_at = datetime.now(UTC)
                        license.invalidation_reason = "whop_subscription_expired"

            await db.commit()

    async def _sync_active_subscriptions(self):
        """Verify active subscriptions are still valid"""
        async with self.db_session_factory() as db:
            from backend.models.license import License

            active_licenses = await db.execute(
                select(License).where(
                    License.status == LicenseStatus.ACTIVE, License.whop_membership_id.isnot(None)
                )
            )

            for license in active_licenses.scalars():
                if license.whop_membership_id:
                    sub = await self.license_service.get_subscription(license.whop_membership_id)
                    if not sub or sub.status not in ["active", "trialing"]:
                        license.status = LicenseStatus.REVOKED
                        license.invalidated_at = datetime.now(UTC)
                        license.invalidation_reason = "whop_subscription_cancelled"

            await db.commit()


class WhopPricingDisplay:
    """Helper for displaying Whop pricing in UI"""

    @staticmethod
    def get_pricing_card(plan_type: WhopProductType) -> dict:
        config = WhopPlanConfig.get_config(plan_type)

        return {
            "id": plan_type.value,
            "name": config["name"],
            "price": config["price_usd"],
            "daily_tokens": config["daily_tokens"],
            "max_devices": config["max_devices"],
            "refill_hours": config.get("refill_hours", 24),
            "features": config["features"],
            "popular": plan_type == WhopProductType.PRO,
        }

    @staticmethod
    def get_all_pricing() -> list[dict]:
        return [WhopPricingDisplay.get_pricing_card(plan_type) for plan_type in WhopProductType]
