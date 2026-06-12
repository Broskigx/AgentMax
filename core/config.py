from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # === Application ===
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    debug: bool = Field(default=False, validation_alias="DEBUG")
    app_name: str = Field(default="AgentMax", validation_alias="APP_NAME")
    app_version: str = Field(default="1.0.0", validation_alias="APP_VERSION")

    # === Server ===
    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    port: int = Field(default=8000, validation_alias="PORT")
    workers: int = Field(default=1, validation_alias="WORKERS")

    # === Database ===
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    database_pool_size: int = Field(default=20, validation_alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=10, validation_alias="DATABASE_MAX_OVERFLOW")
    database_echo: bool = Field(default=False, validation_alias="DATABASE_ECHO")

    # === Redis ===
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")
    redis_max_connections: int = Field(default=50, validation_alias="REDIS_MAX_CONNECTIONS")
    redis_decode_responses: bool = Field(default=True, validation_alias="REDIS_DECODE_RESPONSES")

    # === Security ===
    secret_key: str = Field(default="CHANGE_ME__SECRET_KEY_NOT_SET", validation_alias="SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    jwt_expiration_minutes: int = Field(default=30, validation_alias="JWT_EXPIRATION_MINUTES")
    jwt_refresh_expiration_days: int = Field(
        default=7, validation_alias="JWT_REFRESH_EXPIRATION_DAYS"
    )

    @field_validator("secret_key")
    @classmethod
    def _validate_secret_key(cls, v: str) -> str:
        if v.startswith("CHANGE_ME"):
            raise ValueError(
                "SECRET_KEY is set to the insecure placeholder. "
                "Set a real SECRET_KEY in your .env file."
            )
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long.")
        return v

    # === CORS ===
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"], validation_alias="CORS_ORIGINS"
    )
    cors_allow_credentials: bool = Field(default=True, validation_alias="CORS_ALLOW_CREDENTIALS")
    cors_allow_methods: list[str] = Field(
        default=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], validation_alias="CORS_ALLOW_METHODS"
    )
    cors_allow_headers: list[str] = Field(default=["*"], validation_alias="CORS_ALLOW_HEADERS")

    # === Rate Limiting ===
    rate_limit_enabled: bool = Field(default=True, validation_alias="RATE_LIMIT_ENABLED")
    rate_limit_requests_per_minute: int = Field(default=60, validation_alias="RATE_LIMIT_RPM")
    rate_limit_burst: int = Field(default=10, validation_alias="RATE_LIMIT_BURST")

    # === Admin ===
    admin_secret: str = Field(
        default="CHANGE_ME__ADMIN_SECRET_NOT_SET", validation_alias="ADMIN_SECRET"
    )
    admin_rate_limit: int = Field(default=200, validation_alias="ADMIN_RATE_LIMIT")

    @field_validator("admin_secret")
    @classmethod
    def _validate_admin_secret(cls, v: str) -> str:
        if v.startswith("CHANGE_ME"):
            raise ValueError(
                "ADMIN_SECRET is set to the insecure placeholder. "
                "Set a real ADMIN_SECRET in your .env file."
            )
        return v

    # === Whop Integration ===
    whop_api_key: str | None = Field(default=None, validation_alias="WHOP_API_KEY")
    whop_webhook_secret: str | None = Field(default=None, validation_alias="WHOP_WEBHOOK_SECRET")
    whop_app_id: str | None = Field(default=None, validation_alias="WHOP_APP_ID")
    whop_api_url: str | None = Field(
        default="https://api.whop.com", validation_alias="WHOP_API_URL"
    )
    whop_verify_signature: bool = Field(default=True, validation_alias="WHOP_VERIFY_SIGNATURE")
    whop_sync_interval_minutes: int = Field(default=15, validation_alias="WHOP_SYNC_INTERVAL")

    # === AI Vision ===
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(
        default="https://api.openai.com/v1", validation_alias="OPENAI_BASE_URL"
    )
    claude_api_key: str | None = Field(default=None, validation_alias="CLAUDE_API_KEY")
    vision_local_endpoint: str | None = Field(
        default=None, validation_alias="VISION_LOCAL_ENDPOINT"
    )
    vision_local_model: str | None = Field(default=None, validation_alias="VISION_LOCAL_MODEL")
    vision_timeout: float = Field(default=30.0, validation_alias="VISION_TIMEOUT")
    vision_enable_caching: bool = Field(default=True, validation_alias="VISION_ENABLE_CACHING")

    # === Anti-Crack ===
    enable_anti_crack: bool = Field(default=True, validation_alias="ENABLE_ANTI_CRACK")
    anti_crack_check_interval_ms: int = Field(default=5000, validation_alias="ANTI_CRACK_INTERVAL")
    anti_crack_max_failed_checks: int = Field(default=3, validation_alias="ANTI_CRACK_MAX_FAILS")
    anti_crack_auto_lock: bool = Field(default=True, validation_alias="ANTI_CRACK_AUTO_LOCK")

    # === Logging ===
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: str = Field(default="json", validation_alias="LOG_FORMAT")
    log_structured: bool = Field(default=True, validation_alias="LOG_STRUCTURED")

    # === Telemetry ===
    enable_telemetry: bool = Field(default=True, validation_alias="ENABLE_TELEMETRY")
    telemetry_endpoint: str | None = Field(default=None, validation_alias="TELEMETRY_ENDPOINT")

    # === Feature Flags ===
    feature_automation: bool = Field(default=True, validation_alias="FEATURE_AUTOMATION")
    feature_plugins: bool = Field(default=True, validation_alias="FEATURE_PLUGINS")
    feature_updater: bool = Field(default=True, validation_alias="FEATURE_UPDATER")
    feature_recorder: bool = Field(default=True, validation_alias="FEATURE_RECORDER")
    feature_memory: bool = Field(default=True, validation_alias="FEATURE_MEMORY")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ["production", "prod"]

    @property
    def is_development(self) -> bool:
        return self.environment.lower() in ["development", "dev"]

    @property
    def database_url_async(self) -> str:
        if self.database_url:
            return self.database_url
        return os.getenv(
            "DATABASE_URL", "postgresql+asyncpg://AgentMax:AgentMax@localhost:5432/AgentMax"
        )

    @property
    def redis_url_async(self) -> str:
        if self.redis_url:
            return self.redis_url
        return os.getenv("REDIS_URL", "redis://localhost:6379/0")

    @property
    def cors_origins_list(self) -> list[str]:
        if isinstance(self.cors_origins, str):
            return [origin.strip() for origin in self.cors_origins.split(",")]
        return self.cors_origins

    def get_database_pool_config(self) -> dict:
        return {
            "pool_size": self.database_pool_size,
            "max_overflow": self.database_max_overflow,
            "echo": self.database_echo if self.is_development else False,
        }

    def get_redis_config(self) -> dict:
        return {
            "max_connections": self.redis_max_connections,
            "decode_responses": self.redis_decode_responses,
        }


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    global _settings
    _settings = Settings()
    return _settings


# ──────────────────────────────────────────────────────────────────────────────
# AgentMaxConfig -- nested runtime config used by runtime.py and all agents.
# This is the single source of truth. runtime.py calls get_config().
# ──────────────────────────────────────────────────────────────────────────────


class LicenseConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    dev_bypass: bool = Field(default=False, validation_alias="AGENTMAX_DEV_BYPASS")


class ServerConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    host: str = Field(default="127.0.0.1", validation_alias="AGENTMAX_HOST")
    ws_port: int = Field(default=7788, validation_alias="AGENTMAX_WS_PORT")
    api_port: int = Field(default=7790, validation_alias="AGENTMAX_API_PORT")


class AIConfig(BaseSettings):
    # populate_by_name lets callers construct the config with Python field names
    # (e.g. AIConfig(lmstudio_port=1235)) in addition to the env-var aliases.
    # Without it, field-name kwargs are silently dropped and only .env/env wins.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)
    backend: str = Field(
        default="claude",
        validation_alias=AliasChoices("AGENTMAX_AI_BACKEND", "AGENTMAX_BACKEND"),
    )  # claude | lmstudio | local_peft | llamacpp
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    lmstudio_host: str = Field(default="127.0.0.1", validation_alias="AGENTMAX_LMS_HOST")
    lmstudio_port: int = Field(default=1235, validation_alias="AGENTMAX_LMS_PORT")
    api_base_url: str | None = Field(default=None, validation_alias="AGENTMAX_API_BASE_URL")
    model: str = Field(default="claude-sonnet-4-6", validation_alias="AGENTMAX_MODEL")
    vision_model: str | None = Field(default=None, validation_alias="AGENTMAX_VISION_MODEL")
    max_tokens: int = Field(default=4096, validation_alias="AGENTMAX_MAX_TOKENS")
    temperature: float = Field(default=0.4, validation_alias="AGENTMAX_TEMPERATURE")
    timeout_sec: float = Field(default=300.0, validation_alias="AGENTMAX_TIMEOUT")
    max_retries: int = Field(default=3, validation_alias="AGENTMAX_MAX_RETRIES")
    retry_delay_sec: float = Field(default=1.0, validation_alias="AGENTMAX_RETRY_DELAY")

    # ─── Local PEFT adapter backend (AgentMax V2.1+) ─────────────────────
    # Active when backend == "local_peft". The base model is downloaded /
    # cached via HuggingFace; the LoRA adapter is loaded from disk on top.
    local_peft_base_model: str = Field(
        default="unsloth/Qwen3-VL-8B-Thinking",
        validation_alias="AGENTMAX_LOCAL_PEFT_BASE",
    )
    local_peft_adapter_path: str = Field(
        default="./models/AgentMax/V2.1/adapter",
        validation_alias="AGENTMAX_LOCAL_PEFT_ADAPTER",
    )
    local_peft_load_in_4bit: bool = Field(
        default=True,
        validation_alias="AGENTMAX_LOCAL_PEFT_4BIT",
    )
    local_peft_max_new_tokens: int = Field(
        default=512,
        validation_alias="AGENTMAX_LOCAL_PEFT_MAX_NEW",
    )

    # True GGUF backend through llama.cpp's OpenAI-compatible llama-server.
    # This is intentionally separate from local_peft; no model download or
    # PEFT-to-GGUF conversion happens automatically.
    llama_cpp_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("AGENTMAX_LLAMA_HOST", "AGENTMAX_LLAMACPP_HOST"),
    )
    llama_cpp_port: int = Field(
        default=8080,
        validation_alias=AliasChoices("AGENTMAX_LLAMA_PORT", "AGENTMAX_LLAMACPP_PORT"),
    )
    llama_cpp_server_bin: str = Field(
        default="llama-server",
        validation_alias=AliasChoices(
            "AGENTMAX_LLAMA_SERVER_BIN", "AGENTMAX_LLAMACPP_SERVER_BIN"
        ),
    )
    llama_cpp_model_path: str = Field(
        default="",
        validation_alias=AliasChoices("AGENTMAX_GGUF_MODEL_PATH", "AGENTMAX_LLAMA_MODEL_PATH"),
    )
    llama_cpp_context_size: int = Field(
        default=4096,
        validation_alias=AliasChoices("AGENTMAX_LLAMA_CONTEXT", "AGENTMAX_LLAMA_CTX"),
    )
    llama_cpp_threads: int = Field(default=0, validation_alias="AGENTMAX_LLAMA_THREADS")
    llama_cpp_gpu_layers: int = Field(
        default=0,
        validation_alias="AGENTMAX_LLAMA_GPU_LAYERS",
    )
    llama_cpp_mmproj_path: str = Field(
        default="",
        validation_alias="AGENTMAX_LLAMA_MMPROJ_PATH",
    )

    @property
    def base_url_resolved(self) -> str:
        """Return the resolved base URL for the API provider."""
        if self.api_base_url:
            return self.api_base_url.rstrip("/")
        if self.backend.lower() == "llamacpp":
            return f"http://{self.llama_cpp_host}:{self.llama_cpp_port}/v1"
        return f"http://{self.lmstudio_host}:{self.lmstudio_port}/v1"

    @property
    def effective_vision_model(self) -> str:
        return self.vision_model or self.model

    @staticmethod
    def mask_key(key: str | None) -> str:
        """Mask an API key for safe logging: 'sk-...ab12'"""
        if not key:
            return ""
        if len(key) <= 8:
            return "***"
        return key[:4] + "..." + key[-4:]

    @property
    def safe_anthropic_key(self) -> str:
        return self.mask_key(self.anthropic_api_key)

    @property
    def safe_openai_key(self) -> str:
        return self.mask_key(self.openai_api_key)

    def __repr__(self) -> str:
        return (
            f"AIConfig(backend={self.backend!r}, model={self.model!r}, "
            f"base_url={self.base_url_resolved!r}, "
            f"anthropic_key={self.safe_anthropic_key}, "
            f"openai_key={self.safe_openai_key})"
        )

    def __str__(self) -> str:
        return self.__repr__()


class TokenConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    unlimited: bool = Field(default=False, validation_alias="AGENTMAX_UNLIMITED_TOKENS")
    daily_cap: int = Field(default=0, validation_alias="AGENTMAX_TOKEN_DAILY_CAP")
    monthly_cap: int = Field(default=0, validation_alias="AGENTMAX_TOKEN_MONTHLY_CAP")
    tracking_enabled: bool = Field(default=True, validation_alias="AGENTMAX_TOKEN_TRACKING")
    persist_path: str = Field(default="", validation_alias="AGENTMAX_TOKEN_PERSIST")

    @property
    def effective_unlimited(self) -> bool:
        """Return True if unlimited mode is active (env var or dev bypass)."""
        if self.unlimited:
            return True
        import os

        return os.environ.get("AGENTMAX_DEV_BYPASS", "").strip() in ("1", "true", "yes")


class InputConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    mouse_speed: float = Field(default=0.1, validation_alias="AGENTMAX_MOUSE_SPEED")
    type_interval: float = Field(default=0.05, validation_alias="AGENTMAX_TYPE_INTERVAL")
    mouse_speed_px_per_sec: float = Field(
        default=2200.0, validation_alias="AGENTMAX_MOUSE_SPEED_PX_PER_SEC"
    )
    mouse_jitter_px: float = Field(default=0.15, validation_alias="AGENTMAX_MOUSE_JITTER_PX")
    bezier_control_variance: float = Field(
        default=0.08, validation_alias="AGENTMAX_BEZIER_CONTROL_VARIANCE"
    )
    typing_wpm: float = Field(default=420.0, validation_alias="AGENTMAX_TYPING_WPM")
    typing_variance: float = Field(default=0.08, validation_alias="AGENTMAX_TYPING_VARIANCE")
    pre_click_verify: bool = Field(default=False, validation_alias="AGENTMAX_PRE_CLICK_VERIFY")
    post_click_verify: bool = Field(default=False, validation_alias="AGENTMAX_POST_CLICK_VERIFY")


class VisionConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    screenshot_fps: int = Field(default=10, validation_alias="AGENTMAX_VISION_SCREENSHOT_FPS")
    enable_ocr: bool = Field(default=True, validation_alias="AGENTMAX_VISION_OCR")
    enable_accessibility: bool = Field(
        default=True, validation_alias="AGENTMAX_VISION_ACCESSIBILITY"
    )
    cache_frames: bool = Field(default=True, validation_alias="AGENTMAX_VISION_CACHE_FRAMES")


class MemoryConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    short_term_capacity: int = Field(default=200, validation_alias="AGENTMAX_STM_CAPACITY")
    short_term_ttl_sec: int = Field(default=3600, validation_alias="AGENTMAX_STM_TTL")
    long_term_db_path: str = Field(
        default_factory=lambda: str(Path.home() / ".AgentMax" / "data" / "long_term.db"),
        validation_alias="AGENTMAX_LTM_PATH",
    )
    long_term_persist_dir: str = Field(
        default_factory=lambda: str(Path.home() / ".AgentMax" / "data" / "chroma"),
        validation_alias="AGENTMAX_LTM_PERSIST_DIR",
    )
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2", validation_alias="AGENTMAX_EMBEDDING_MODEL"
    )
    visual_memory_path: str = Field(
        default_factory=lambda: str(Path.home() / ".AgentMax" / "data" / "visual_memory"),
        validation_alias="AGENTMAX_VISUAL_MEM_PATH",
    )
    max_visual_templates: int = Field(default=500, validation_alias="AGENTMAX_MAX_VISUAL_TEMPLATES")


class SecurityConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    panic_key: str = Field(default="ctrl+shift+x", validation_alias="AGENTMAX_PANIC_KEY")
    audit_log: str = Field(default="./data/audit.log", validation_alias="AGENTMAX_AUDIT_LOG")
    enable_anti_crack: bool = Field(default=True, validation_alias="ENABLE_ANTI_CRACK")
    enable_air_gap: bool = Field(default=True, validation_alias="AGENTMAX_AIR_GAP")
    require_consent: bool = Field(default=True, validation_alias="AGENTMAX_REQUIRE_CONSENT")
    max_actions_per_minute: int = Field(default=60, validation_alias="AGENTMAX_MAX_ACTIONS_PM")
    audit_hmac_key: str = Field(default="", validation_alias="AGENTMAX_AUDIT_HMAC_KEY")
    allowed_fs_dirs: list[str] = Field(
        default_factory=list, validation_alias="AGENTMAX_ALLOWED_FS_DIRS"
    )


class AgentMaxConfig(BaseSettings):
    """
    Top-level configuration for AgentMax runtime.

    All values read from environment variables or .env file.
    Nested sections mirror the subsystems in runtime.py.

    Usage::

        from core.config import get_config
        config = get_config()
        config.ai.backend          # 'claude' | 'lmstudio'
        config.server.ws_port      # 7788
        config.license.dev_bypass  # False
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    debug: bool = Field(default=False, validation_alias="DEBUG")
    version: str = Field(default="1.0.0", validation_alias="APP_VERSION")
    telemetry_enabled: bool = Field(default=True, validation_alias="ENABLE_TELEMETRY")
    update_channel: str = Field(default="stable", validation_alias="AGENTMAX_UPDATE_CHANNEL")
    # AgentMax observability layer (thinking-strip + token logs + bus events).
    # ON by default; set AGENTMAX_OBSERVABILITY=0 to fall back to
    # legacy behaviour.
    AgentMax_observability: bool = Field(
        default=True,
        validation_alias="AGENTMAX_OBSERVABILITY",
    )
    # IPC authentication (X-AgentMax-Token header + WS handshake). ON by default:
    # the token is auto-generated and persisted to the OS token file (and env) so
    # local clients (the CLI and the desktop UI) can read it. Disable with
    # AGENTMAX_IPC_AUTH=0.
    ipc_auth_enabled: bool = Field(
        default=True,
        validation_alias="AGENTMAX_IPC_AUTH",
    )

    # Nested sections -- each reads from env vars with its own prefix
    license: LicenseConfig = Field(default_factory=LicenseConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    input: InputConfig = Field(default_factory=InputConfig)
    tokens: TokenConfig = Field(default_factory=TokenConfig)

    @property
    def is_production(self) -> bool:
        return not self.debug

    @property
    def host(self) -> str:
        """Convenience shortcut used by the IPC server."""
        return self.server.host

    @property
    def ws_port(self) -> int:
        """Convenience shortcut used by the IPC server."""
        return self.server.ws_port


_config: AgentMaxConfig | None = None


def get_config() -> AgentMaxConfig:
    """
    Return the singleton AgentMaxConfig instance.

    Used by runtime.py and all agents. Reads .env on first call.
    """
    global _config
    if _config is None:
        _config = AgentMaxConfig()
    return _config


def reload_config() -> AgentMaxConfig:
    """Force reload config from environment (e.g. after .env changes)."""
    global _config
    _config = AgentMaxConfig()
    return _config
