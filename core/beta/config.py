"""Closed beta configuration loader.

Precedence: defaults < agentmax.config.json < beta_config.json < AGENTMAX_* env.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.feature_flags import FeatureFlags, load_config_profile

ROOT = Path(__file__).resolve().parents[2]


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "si"}


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path, errors: list[str] | None = None) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        if errors is not None:
            errors.append(f"{path.name}: read failed: {exc}")
        return {}
    except json.JSONDecodeError as exc:
        if errors is not None:
            errors.append(
                f"{path.name}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
            )
        return {}
    if not isinstance(data, dict):
        if errors is not None:
            errors.append(f"{path.name}: expected JSON object")
        return {}
    return data


@dataclass
class BetaConfig:
    app_name: str = "AgentMax"
    app_version: str = "0.1.0-beta.1"
    build_number: str = "local-dev"
    app_env: str = "local"
    beta_mode: bool = True
    beta_data_optin: bool = False
    api_port: int = 7790
    ws_port: int = 7788
    lms_host: str = "127.0.0.1"
    lms_port: int = 1235
    model: str = "AgentMax-test"
    sqlite_path: str = "data/agentmax_beta.sqlite"
    redis_url: str = "redis://localhost:6379/0"
    telemetry_enabled: bool = False
    ipc_auth_enabled: bool = True
    sentry_dsn: str | None = None
    agentpilot_endpoint: str = "http://127.0.0.1:1235/v1/chat/completions"
    local_model_endpoint: str = "http://127.0.0.1:1234"
    log_level: str = "INFO"
    config_errors: list[str] = field(default_factory=list)
    feature_flags: FeatureFlags = field(default_factory=FeatureFlags)

    @property
    def data_dir(self) -> Path:
        """Resolve user-writable data directory. Prefers AGENTMAX_DATA_DIR (set by Tauri on first-run for any user)."""
        env = os.environ.get("AGENTMAX_DATA_DIR")
        if env:
            return Path(env)
        local_app_data = os.environ.get("LOCALAPPDATA")
        if os.name == "nt" and local_app_data:
            return Path(local_app_data) / "AgentMax"
        return Path.home() / ".agentmax"

    @property
    def sqlite_abs_path(self) -> Path:
        path = Path(self.sqlite_path)
        if path.is_absolute():
            return path
        base = self.data_dir
        (base / "data").mkdir(parents=True, exist_ok=True)
        return base / path

    @property
    def safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["feature_flags"] = asdict(self.feature_flags)
        if data.get("sentry_dsn"):
            data["sentry_dsn"] = "<SECRET>"
        return data


def load_beta_config(root: Path | None = None) -> BetaConfig:
    base = root or ROOT
    profile = load_config_profile(base)
    merged = dict(profile.data)
    config = BetaConfig(feature_flags=profile.feature_flags, config_errors=profile.errors)

    # --- User-adaptive paths (no hardcoded developer paths) ---
    # Tauri sets AGENTMAX_DATA_DIR on every launch using proper app_data_dir().
    # This makes the app adapt to ANY user/install on Windows/macOS/Linux.
    data_dir = config.data_dir
    if "sqlite_path" in merged or not config.sqlite_path:
        rel = merged.get("sqlite_path", "data/agentmax_beta.sqlite")
        p = Path(rel)
        config.sqlite_path = str(p if p.is_absolute() else (data_dir / p))

    simple_keys = {
        "app_env": "AGENTMAX_APP_ENV",
        "build_number": "AGENTMAX_BUILD_NUMBER",
        "lms_host": "AGENTMAX_LMS_HOST",
        "model": "AGENTMAX_MODEL",
        "sqlite_path": "AGENTMAX_SQLITE_PATH",
        "redis_url": "AGENTMAX_REDIS_URL",
        "sentry_dsn": "AGENTMAX_SENTRY_DSN",
        "agentpilot_endpoint": "AGENTMAX_AGENTPILOT_ENDPOINT",
        "local_model_endpoint": "AGENTMAX_LOCAL_MODEL_ENDPOINT",
        "log_level": "AGENTMAX_LOG_LEVEL",
    }
    for key in asdict(config):
        if key in {"feature_flags", "config_errors"}:
            continue
        if key in merged and hasattr(config, key):
            setattr(config, key, merged[key])

    for key, env_key in simple_keys.items():
        if env_key in os.environ:
            setattr(config, key, os.environ[env_key])

    # Re-resolve sqlite after env overrides for closed-source portable installs
    p = Path(config.sqlite_path)
    if not p.is_absolute():
        config.sqlite_path = str(config.data_dir / p)

    int_keys = {
        "api_port": "AGENTMAX_API_PORT",
        "ws_port": "AGENTMAX_WS_PORT",
        "lms_port": "AGENTMAX_LMS_PORT",
    }
    for key, env_key in int_keys.items():
        if key in merged:
            setattr(config, key, _int_value(merged[key], getattr(config, key)))
        if env_key in os.environ:
            setattr(config, key, _int_value(os.environ[env_key], getattr(config, key)))

    if "AGENTMAX_BETA_MODE" in os.environ:
        config.beta_mode = _truthy(os.environ["AGENTMAX_BETA_MODE"])
    elif "beta_mode" in merged:
        config.beta_mode = bool(merged["beta_mode"])

    if "AGENTMAX_BETA_DATA_OPTIN" in os.environ:
        config.beta_data_optin = _truthy(os.environ["AGENTMAX_BETA_DATA_OPTIN"])
    elif "beta_data_optin" in merged:
        config.beta_data_optin = bool(merged["beta_data_optin"])

    if "AGENTMAX_TELEMETRY_ENABLED" in os.environ:
        config.telemetry_enabled = _truthy(os.environ["AGENTMAX_TELEMETRY_ENABLED"])
    elif "telemetry_enabled" in merged:
        config.telemetry_enabled = bool(merged["telemetry_enabled"])

    if "AGENTMAX_IPC_AUTH" in os.environ:
        config.ipc_auth_enabled = _truthy(os.environ["AGENTMAX_IPC_AUTH"])
    elif "ipc_auth_enabled" in merged:
        config.ipc_auth_enabled = bool(merged["ipc_auth_enabled"])

    return config


_CONFIG: BetaConfig | None = None


def get_beta_config() -> BetaConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_beta_config()
    return _CONFIG


def reload_beta_config() -> BetaConfig:
    global _CONFIG
    _CONFIG = load_beta_config()
    return _CONFIG
