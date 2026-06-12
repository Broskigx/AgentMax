"""Beta REST routes shared by the runtime IPC bridge and the legacy HTTP server."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from core.beta.config import ROOT, get_beta_config
from core.beta.diagnostics import export_diagnostics_bundle
from core.beta.logging_service import BetaLogger
from core.beta.redis_service import RedisService
from core.beta.smoke import run_beta_smoke_test
from core.beta.storage import StorageService

TESTER_ID_FILE = ROOT / "data" / "agentmax_tester_id.txt"

RUNTIME_MODE = os.environ.get("AGENTMAX_RUNTIME_MODE", "full").strip().lower()
BACKEND_ID = "agentmax" if RUNTIME_MODE == "full" else f"agentmax_{RUNTIME_MODE}"
APP_VERSION = "0.1.1" if RUNTIME_MODE == "full" else "0.1.1-limited"

_STORAGE: StorageService | None = None
_LOGGER: BetaLogger | None = None
_REDIS: RedisService | None = None


def _storage() -> StorageService:
    global _STORAGE
    if _STORAGE is None:
        store = StorageService(config=get_beta_config())
        store.migrate()
        _STORAGE = store
    return _STORAGE


def _logger() -> BetaLogger:
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = BetaLogger(storage=_storage())
    return _LOGGER


def _redis() -> RedisService:
    global _REDIS
    if _REDIS is None:
        service = RedisService(config=get_beta_config(), storage=_storage())
        service.connect()
        _REDIS = service
    return _REDIS


def _normalize_user(value: Any, *, allow_generated: bool = True) -> str:
    raw = str(value or "").strip()[:64]
    clean = re.sub(r"[^a-zA-Z0-9_.-]", "", raw)
    if clean:
        return clean
    return local_tester_id() if allow_generated else ""


def local_tester_id() -> str:
    """Stable per-machine tester id (no hardcoded developer username)."""
    if TESTER_ID_FILE.exists():
        try:
            existing = TESTER_ID_FILE.read_text(encoding="utf-8").strip()
            normalized = _normalize_user(existing, allow_generated=False)
            if normalized:
                return normalized
        except OSError:
            pass
    generated = f"tester-{uuid.uuid4().hex[:12]}"
    TESTER_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    TESTER_ID_FILE.write_text(generated, encoding="utf-8")
    return generated


def request_user(
    data: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    query: dict[str, list[str]] | None = None,
) -> str:
    header_user = ""
    if headers:
        header_user = headers.get("X-AgentMax-User") or headers.get("x-agentmax-user") or ""
    query_user = (query or {}).get("user", [""])[0] if query else ""
    body_user = data.get("user", "") if data else ""
    return _normalize_user(body_user or header_user or query_user or local_tester_id())


def health_payload(*, ipc_auth_enabled: bool) -> dict[str, Any]:
    cfg = get_beta_config()
    limited = RUNTIME_MODE != "full"
    return {
        "ok": True,
        "status": "ok",
        "version": APP_VERSION,
        "backend": BACKEND_ID,
        "ipc_auth_enabled": ipc_auth_enabled,
        "tester_id": local_tester_id(),
        "runtime_mode": RUNTIME_MODE,
        "limited": limited,
        "fallback_reason": os.environ.get("AGENTMAX_FALLBACK_REASON") if limited else None,
        "capabilities": {
            "screen_vision": cfg.feature_flags.screen_vision,
            "mouse_control": cfg.feature_flags.mouse_control and not limited,
            "keyboard_control": cfg.feature_flags.keyboard_control and not limited,
            "terminal": cfg.feature_flags.terminal and not limited,
            "file_actions": cfg.feature_flags.file_actions and not limited,
        },
    }


def beta_status_payload() -> dict[str, Any]:
    cfg = get_beta_config()
    storage = _storage()
    logger = _logger()
    redis_status = _redis().status()
    return {
        "ok": True,
        "status": "running",
        "app": {
            "name": cfg.app_name,
            "version": cfg.app_version,
            "build_number": cfg.build_number,
            "environment": cfg.app_env,
            "beta_mode": cfg.beta_mode,
        },
        "config": cfg.safe_dict,
        "sqlite": storage.status(),
        "redis": redis_status.__dict__,
        "agentpilot": {
            "endpoint": cfg.agentpilot_endpoint,
            "local": cfg.feature_flags.local_agentpilot,
            "cloud": cfg.feature_flags.cloud_agentpilot,
        },
        "recent_tasks": storage.recent_tasks(10),
        "recent_logs": {
            "app": logger.recent("app", 10),
            "errors": logger.recent("errors", 10),
            "feedback": logger.recent("beta_feedback", 10),
        },
    }


def try_handle(
    method: str,
    path: str,
    data: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    query: dict[str, list[str]] | None = None,
) -> dict[str, Any] | None:
    """Return a response dict when this module handles the route, else None."""
    if method == "GET":
        if path == "/api/beta/status":
            return beta_status_payload()
        if path == "/api/beta/config":
            return get_beta_config().safe_dict
        if path == "/api/tasks/recent":
            limit_raw = (query or {}).get("limit", ["20"])[0]
            try:
                task_limit = int(limit_raw)
            except (TypeError, ValueError):
                task_limit = 20
            return {"tasks": _storage().recent_tasks(task_limit)}

    if method != "POST":
        return None

    if path == "/api/feedback":
        storage = _storage()
        user = storage.ensure_user(user_id=request_user(data, headers=headers, query=query))
        rating_raw = (data or {}).get("rating")
        try:
            rating = int(rating_raw) if rating_raw is not None else None
        except (TypeError, ValueError):
            rating = None
        feedback_id = storage.save_feedback(
            user_id=user,
            conversation_id=str((data or {}).get("conversation_id") or "") or None,
            task_id=str((data or {}).get("task_id") or "") or None,
            rating=rating,
            category=str((data or {}).get("category") or "other"),
            message=str((data or {}).get("message") or ""),
            screenshot_path=str((data or {}).get("screenshot_path") or "") or None,
            logs_path=str((data or {}).get("logs_path") or "") or None,
        )
        _logger().log(
            "beta_feedback",
            level="info",
            source="api",
            event="feedback.saved",
            metadata={"feedback_id": feedback_id, "category": (data or {}).get("category")},
        )
        return {"ok": True, "feedback_id": feedback_id}

    if path == "/api/diagnostics/export":
        result = export_diagnostics_bundle(storage=_storage())
        _logger().log(
            "app",
            level="info",
            source="api",
            event="diagnostics.exported",
            metadata={"zip_path": result.get("zip_path")},
        )
        return result

    if path == "/api/beta/smoke-test":
        result = run_beta_smoke_test()
        _logger().log(
            "app",
            level="info" if result.get("ok") else "error",
            source="api",
            event="beta.smoke_test",
            metadata={"checks": result.get("checks")},
        )
        return result

    if path == "/api/beta/tester-access":
        storage = _storage()
        user = storage.ensure_user(user_id=request_user(data, headers=headers, query=query))
        enabled = str((data or {}).get("enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
        storage.set_setting("tester.full_agentpilot_access", enabled, user_id=user)
        storage.add_agent_event(
            task_id=None,
            event_type="tester.full_agentpilot_access",
            payload={"user": user, "enabled": enabled, "control_consent": "per_action"},
        )
        _logger().log(
            "app",
            level="info",
            source="api",
            event="tester.full_agentpilot_access",
            metadata={"enabled": enabled, "control_consent": "per_action"},
        )
        return {
            "ok": True,
            "enabled": enabled,
            "access": "full" if enabled else "standard",
            "control_consent": "per_action",
        }

    return None
