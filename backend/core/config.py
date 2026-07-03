"""Central configuration -- all settings from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Environment ───────────────────────────────────────────────────────────
    environment: Literal["development", "staging", "production"] = "production"

    # ── Database ──────────────────────────────────────────────────────────────
    _POSTGRES_SCHEMES: ClassVar[tuple[str, ...]] = (
        "postgresql://",
        "postgresql+asyncpg://",
        "postgresql+psycopg://",
        "postgres://",
    )
    _LOCAL_TEST_SCHEMES: ClassVar[tuple[str, ...]] = ("sqlite+aiosqlite://",)

    database_url: str = Field(
        ...,
        description="Async database DSN. PostgreSQL is required in production.",
        examples=["postgresql+asyncpg://user:pass@localhost:5432/AgentMax"],
    )
    database_pool_size: int = 10
    database_pool_max_overflow: int = 20
    database_pool_timeout: int = 30

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: RedisDsn = Field(
        default="redis://localhost:6379/0",
        description="Redis DSN.  Used for rate-limiting and challenge cache.",
    )
    redis_max_connections: int = 50

    # ── Cryptography ──────────────────────────────────────────────────────────
    # Ed25519 private key in PEM format (base64-encoded in env).
    # Generate: python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; k=Ed25519PrivateKey.generate(); print(k.private_bytes_raw().hex())"
    ed25519_private_key_hex: str = Field(
        ...,
        description="64-char hex of Ed25519 private key seed.",
        min_length=64,
        max_length=64,
    )

    # AES-256 master key for encrypting refresh tokens at rest.
    aes_master_key_hex: str = Field(
        ...,
        description="64-char hex of 32-byte AES-256 master key.",
        min_length=64,
        max_length=64,
    )

    # ── JWT ───────────────────────────────────────────────────────────────────
    access_token_ttl_seconds: int = 3600  # 1 hour
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days
    offline_token_ttl_seconds: int = 60 * 60 * 24  # 24 hours
    challenge_ttl_seconds: int = 300  # 5 minutes

    # ── Anti-crack ────────────────────────────────────────────────────────────
    enable_anti_crack: bool = False
    """Enable runtime anti-tamper checks (debugger, VM, integrity)."""

    rate_limit_enabled: bool = True
    """Enable/disable all rate limiting."""

    jwt_expiration_minutes: int = 60
    """JWT access token expiration in minutes (default: 60)."""

    rate_limit_requests_per_minute: int = 120
    """Default rate limit for non-specific API endpoints (default: 120 req/min)."""

    # ── Rate limiting ─────────────────────────────────────────────────────────
    rate_limit_auth_per_minute: int = 10  # per IP on /auth/* endpoints
    rate_limit_api_per_minute: int = 120  # per token on /license/* endpoints
    rate_limit_admin_per_minute: int = 300  # per admin session

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    cors_origins: list[str] = Field(default_factory=list)
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description="Proxy IPs allowed to supply X-Forwarded-For. Empty means never trust forwarded client IPs.",
    )

    # ── Admin ─────────────────────────────────────────────────────────────────
    admin_secret: str = Field(
        ...,
        description="Shared secret for initial admin bootstrap.",
        min_length=32,
    )

    # ── Telemetry sink ───────────────────────────────────────────────────────
    telemetry_enabled: bool = True
    telemetry_max_batch: int = 100

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @field_validator("ed25519_private_key_hex", "aes_master_key_hex")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        try:
            bytes.fromhex(v)
        except ValueError as exc:
            raise ValueError("Must be a valid hexadecimal string") from exc
        return v.lower()

    @model_validator(mode="after")
    def _validate_database_url(self) -> Settings:
        value = self.database_url.strip()
        lowered = value.lower()
        allowed = self._POSTGRES_SCHEMES
        if not self.is_production:
            allowed = (*allowed, *self._LOCAL_TEST_SCHEMES)

        if not lowered.startswith(allowed):
            allowed_text = ", ".join(allowed)
            raise ValueError(f"DATABASE_URL must start with one of: {allowed_text}")

        if self.is_production and not lowered.startswith(self._POSTGRES_SCHEMES):
            raise ValueError("Production DATABASE_URL must use PostgreSQL")

        self.database_url = value
        return self

    def ed25519_private_key_bytes(self) -> bytes:
        return bytes.fromhex(self.ed25519_private_key_hex)

    def aes_master_key_bytes(self) -> bytes:
        return bytes.fromhex(self.aes_master_key_hex)

    # Key data dir for caching ephemeral state
    data_dir: Path = Path("./data")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
