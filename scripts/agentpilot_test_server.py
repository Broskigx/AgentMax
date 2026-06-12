#!/usr/bin/env python3
"""Servidor local de prueba para la primera corrida de AgentMax.

No usa modelos, claves externas, PostgreSQL ni Redis. Expone:
- API AgentMax en http://127.0.0.1:7790
- API OpenAI-compatible en http://127.0.0.1:1235/v1
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import signal
import struct
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.beta import BetaLogger, RedisService, StorageService, export_diagnostics_bundle, get_beta_config
from core.beta.smoke import run_beta_smoke_test
from core.data_collection import consent as beta_consent
from core.data_collection.redactor import redact_record, redact_text
from core.security.ipc_auth import IPCAuthError, check_rest_request, ensure_token

TASKS: dict[str, dict[str, Any]] = {}
STARTED_AT = time.time()
TOKEN_FILE = Path("data") / "agentmax_test_tokens.json"
TOKEN_LOCK = threading.Lock()
DATASET_DIR = Path("data") / "agentmax_training"
DATASET_FILE = DATASET_DIR / "examples.jsonl"
DATASET_SETTINGS_FILE = DATASET_DIR / "settings.json"
DATASET_IMAGE_DIR = DATASET_DIR / "images"
DATASET_LOCK = threading.Lock()
INITIAL_TOKENS = 5000
TESTER_ID_FILE = Path("data") / "agentmax_tester_id.txt"
MAX_REQUEST_TOKENS = 900
ADMIN_KEY = os.environ.get("AGENTMAX_ADMIN_KEY", "")
BETA_CONFIG = get_beta_config()
if BETA_CONFIG.config_errors:
    raise RuntimeError(
        "Invalid AgentMax configuration: " + "; ".join(BETA_CONFIG.config_errors)
    )
DATASET_ENABLED = os.environ.get("AGENTMAX_DATASET_ENABLED", "0").strip().lower() not in {
    "0",
    "false",
    "no",
} and BETA_CONFIG.beta_data_optin and beta_consent.is_enabled()
DATASET_STORE_IMAGES = os.environ.get("AGENTMAX_DATASET_STORE_IMAGES", "0").strip().lower() in {
    "1",
    "true",
    "yes",
}
RUNTIME_MODE = os.environ.get("AGENTMAX_RUNTIME_MODE", "beta_fallback").strip().lower()
LIMITED_MODE = RUNTIME_MODE != "full"
PRODUCT_MODE = not LIMITED_MODE
BACKEND_ID = "agentmax" if PRODUCT_MODE else f"agentmax_{RUNTIME_MODE}"
APP_VERSION = "0.1.1" if PRODUCT_MODE else "0.1.1-limited"
DEFAULT_MODEL_ID = "agentmax-local" if PRODUCT_MODE else "AgentMax-limited"
VISION_MODEL_ID = "agentmax-vision" if PRODUCT_MODE else "AgentMax-vision-metadata"
TASK_COMPLETED_MSG = (
    "Tarea registrada y completada por el runtime local. "
    "El control de escritorio requiere permiso explicito en la app."
    if PRODUCT_MODE
    else "Tarea registrada en modo prueba. No se ejecuto control real del escritorio."
)
BETA_STORAGE = StorageService(config=BETA_CONFIG)
BETA_STORAGE.migrate()
BETA_LOGGER = BetaLogger(storage=BETA_STORAGE)
BETA_REDIS = RedisService(config=BETA_CONFIG, storage=BETA_STORAGE)
BETA_REDIS.connect()
IPC_AUTH_ENABLED = BETA_CONFIG.ipc_auth_enabled
IPC_TOKEN = ensure_token() if IPC_AUTH_ENABLED else ""
ACTION_RE = re.compile(
    r"\b(abre|abrir|ejecuta|busca|crea|lee|lista|borra|mueve|click|clic|terminal|powershell)\b"
)
VISION_RE = re.compile(
    r"\b(imagen|foto|captura|screenshot|pantalla|ocr|visual|vision|ver|analiza)\b"
)
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|clave)\s*[:=]\s*['\"]?([^\s,'\"]{6,})"),
    re.compile(r"(?i)\b(bearer\s+)[a-z0-9._\-]{16,}"),
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b"),
]


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type, Authorization, X-AgentMax-User, X-AgentMax-Admin, X-AgentMax-Token",
    )
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {"message": raw}


def _latest_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
                        parts.append(str(item.get("text") or ""))
                return "\n".join(parts).strip()
            return str(content).strip()
    return ""


def _images_from_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            image_url = item.get("image_url") or {}
            if isinstance(image_url, dict) and image_url.get("url"):
                attachments.append({"data_url": str(image_url.get("url")), "name": "openai-image"})
            elif item.get("type") in {"image_url", "input_image"} and item.get("url"):
                attachments.append({"data_url": str(item.get("url")), "name": "openai-image"})
    return _extract_images({"attachments": attachments})


def _path(handler: BaseHTTPRequestHandler) -> str:
    return urlparse(handler.path).path


def _ipc_authorized(handler: BaseHTTPRequestHandler, path: str) -> bool:
    try:
        check_rest_request(
            path=path,
            headers=dict(handler.headers.items()),
            enabled=IPC_AUTH_ENABLED,
            expected_token=IPC_TOKEN,
        )
        return True
    except IPCAuthError:
        _json_response(
            handler,
            401,
            {"error": "unauthorized", "reason": "missing_or_invalid_token"},
        )
        return False


def _query(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
    return parse_qs(urlparse(handler.path).query)


def _local_tester_id() -> str:
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


def _normalize_user(value: Any, *, allow_generated: bool = True) -> str:
    raw = str(value or "").strip()[:64]
    clean = re.sub(r"[^a-zA-Z0-9_.-]", "", raw)
    if clean:
        return clean
    return _local_tester_id() if allow_generated else ""


def _request_user(handler: BaseHTTPRequestHandler, data: dict[str, Any] | None = None) -> str:
    query_user = (_query(handler).get("user") or [""])[0]
    header_user = handler.headers.get("X-AgentMax-User", "")
    body_user = data.get("user", "") if data else ""
    return _normalize_user(body_user or header_user or query_user or _local_tester_id())


def _admin_authorized(handler: BaseHTTPRequestHandler, data: dict[str, Any]) -> bool:
    provided = str(data.get("admin_key") or handler.headers.get("X-AgentMax-Admin") or "")
    return bool(ADMIN_KEY) and provided == ADMIN_KEY


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_user(user: str) -> str:
    return hashlib.sha256(_normalize_user(user).encode("utf-8")).hexdigest()[:16]


def _redact_text(value: Any, max_len: int = 4000) -> str:
    return redact_text(value)[:max_len]


def _dataset_settings() -> dict[str, Any]:
    defaults = {
        "enabled": DATASET_ENABLED,
        "store_images": DATASET_STORE_IMAGES,
        "redact_text": True,
        "path": str(DATASET_FILE),
        "image_dir": str(DATASET_IMAGE_DIR),
        "schema_version": "AgentMax.training.v1",
    }
    if not DATASET_SETTINGS_FILE.exists():
        return defaults
    try:
        data = json.loads(DATASET_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    merged = {**defaults, **data}
    merged["enabled"] = (
        DATASET_ENABLED
        and bool(merged.get("enabled"))
        and beta_consent.is_enabled()
    )
    merged["store_images"] = bool(merged["enabled"] and merged.get("store_images"))
    return merged


def _beta_status_payload() -> dict[str, Any]:
    redis_status = BETA_REDIS.status()
    return {
        "ok": True,
        "status": "running",
        "app": {
            "name": BETA_CONFIG.app_name,
            "version": BETA_CONFIG.app_version,
            "build_number": BETA_CONFIG.build_number,
            "environment": BETA_CONFIG.app_env,
            "beta_mode": BETA_CONFIG.beta_mode,
        },
        "config": BETA_CONFIG.safe_dict,
        "sqlite": BETA_STORAGE.status(),
        "redis": redis_status.__dict__,
        "agentpilot": {
            "endpoint": BETA_CONFIG.agentpilot_endpoint,
            "local": BETA_CONFIG.feature_flags.local_agentpilot,
            "cloud": BETA_CONFIG.feature_flags.cloud_agentpilot,
        },
        "recent_tasks": BETA_STORAGE.recent_tasks(10),
        "recent_logs": {
            "app": BETA_LOGGER.recent("app", 10),
            "errors": BETA_LOGGER.recent("errors", 10),
            "feedback": BETA_LOGGER.recent("beta_feedback", 10),
        },
    }


def _save_dataset_settings(settings: dict[str, Any]) -> dict[str, Any]:
    clean = {
        "enabled": (
            DATASET_ENABLED
            and bool(settings.get("enabled", False))
            and beta_consent.is_enabled()
        ),
        "store_images": bool(settings.get("store_images", False))
        and beta_consent.is_enabled(),
        "redact_text": bool(settings.get("redact_text", True)),
        "schema_version": "AgentMax.training.v1",
    }
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    DATASET_SETTINGS_FILE.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return _dataset_settings()


def _dataset_count() -> int:
    if not DATASET_FILE.exists():
        return 0
    try:
        with DATASET_FILE.open("r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _append_dataset_example(example: dict[str, Any]) -> bool:
    settings = _dataset_settings()
    if not settings.get("enabled", False) or not beta_consent.is_enabled():
        return False
    record = {
        "schema_version": settings["schema_version"],
        "id": f"ex-{uuid.uuid4().hex}",
        "created_at": _now_iso(),
        **redact_record(example),
    }
    with DATASET_LOCK:
        DATASET_DIR.mkdir(parents=True, exist_ok=True)
        with DATASET_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return True


def _extract_data_url(value: str) -> tuple[str, str]:
    if value.startswith("data:") and ";base64," in value:
        header, payload = value.split(";base64,", 1)
        return header[5:] or "application/octet-stream", payload
    return "", value


def _jpeg_dimensions(raw: bytes) -> tuple[int | None, int | None]:
    if not raw.startswith(b"\xff\xd8"):
        return None, None
    i = 2
    while i + 9 < len(raw):
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        i += 2
        if marker in {0xD8, 0xD9}:
            continue
        if i + 2 > len(raw):
            break
        length = int.from_bytes(raw[i : i + 2], "big")
        if length < 2 or i + length > len(raw):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(raw[i + 3 : i + 5], "big")
            width = int.from_bytes(raw[i + 5 : i + 7], "big")
            return width, height
        i += length
    return None, None


def _webp_dimensions(raw: bytes) -> tuple[int | None, int | None]:
    if len(raw) < 30 or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        return None, None
    chunk = raw[12:16]
    if chunk == b"VP8X" and len(raw) >= 30:
        width = 1 + int.from_bytes(raw[24:27], "little")
        height = 1 + int.from_bytes(raw[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(raw) >= 30:
        width = struct.unpack_from("<H", raw, 26)[0] & 0x3FFF
        height = struct.unpack_from("<H", raw, 28)[0] & 0x3FFF
        return width, height
    if chunk == b"VP8L" and len(raw) >= 25:
        bits = int.from_bytes(raw[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    return None, None


def _detect_image(raw: bytes, mime_hint: str = "", name: str = "") -> dict[str, Any]:
    kind = "unknown"
    mime = mime_hint or "application/octet-stream"
    width: int | None = None
    height: int | None = None
    ext = "bin"
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        kind, mime, ext = "png", "image/png", "png"
        width = int.from_bytes(raw[16:20], "big")
        height = int.from_bytes(raw[20:24], "big")
    elif raw.startswith((b"GIF87a", b"GIF89a")) and len(raw) >= 10:
        kind, mime, ext = "gif", "image/gif", "gif"
        width = int.from_bytes(raw[6:8], "little")
        height = int.from_bytes(raw[8:10], "little")
    elif raw.startswith(b"\xff\xd8"):
        kind, mime, ext = "jpeg", "image/jpeg", "jpg"
        width, height = _jpeg_dimensions(raw)
    elif len(raw) >= 16 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        kind, mime, ext = "webp", "image/webp", "webp"
        width, height = _webp_dimensions(raw)

    digest = hashlib.sha256(raw).hexdigest()
    metadata = {
        "id": f"img-{digest[:16]}",
        "name": _redact_text(name, 120),
        "kind": kind,
        "mime": mime,
        "bytes": len(raw),
        "sha256": digest,
        "width": width,
        "height": height,
        "stored": False,
    }
    settings = _dataset_settings()
    if settings.get("enabled", False) and settings.get("store_images", False) and kind != "unknown":
        DATASET_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        path = DATASET_IMAGE_DIR / f"{digest}.{ext}"
        if not path.exists():
            path.write_bytes(raw)
        metadata["stored"] = True
        metadata["path"] = str(path)
    return metadata


def _extract_images(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = data.get("images") or data.get("attachments") or []
    if not isinstance(raw_items, list):
        return []
    images: list[dict[str, Any]] = []
    for item in raw_items[:4]:
        if isinstance(item, str):
            mime_hint, payload = _extract_data_url(item)
            name = ""
        elif isinstance(item, dict):
            source = str(
                item.get("data_url")
                or item.get("dataUrl")
                or item.get("data")
                or item.get("base64")
                or ""
            )
            mime_hint, payload = _extract_data_url(source)
            mime_hint = str(item.get("mime") or item.get("type") or mime_hint)
            name = str(item.get("name") or "")
        else:
            continue
        if not payload:
            continue
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            continue
        if not raw or len(raw) > 8_000_000:
            continue
        metadata = _detect_image(raw, mime_hint, name)
        if metadata["kind"] != "unknown":
            images.append(metadata)
    return images


def _training_record(
    *,
    kind: str,
    user: str,
    prompt: str,
    reply: str = "",
    status: str,
    error: str | None = None,
    thinking_core: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    tokens: dict[str, Any] | None = None,
    images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    safe_prompt = _redact_text(prompt)
    safe_reply = _redact_text(reply)
    safe_error = _redact_text(error or "", 1600) if error else None
    image_meta = images or []
    return {
        "kind": kind,
        "source": "AgentMax_test_server",
        "user_hash": _hash_user(user),
        "status": status,
        "prompt": safe_prompt,
        "assistant": safe_reply,
        "error": safe_error,
        "thinking_core": thinking_core or {},
        "usage": usage or {},
        "tokens": {
            "charged": (usage or {}).get(
                "total_tokens", (thinking_core or {}).get("token_usage", 0)
            ),
            "remaining": (tokens or {}).get("balance"),
        },
        "images": image_meta,
        "labels": {
            "needs_tools": bool(ACTION_RE.search(prompt.lower())),
            "needs_vision": bool(image_meta) or bool(VISION_RE.search(prompt.lower())),
            "failure_type": safe_error
            or (
                "vision_metadata_only" if image_meta and "metadata" in safe_reply.lower() else None
            ),
        },
        "training": {
            "messages": [
                {"role": "user", "content": safe_prompt, "images": image_meta},
                {"role": "assistant", "content": safe_reply},
            ],
            "ideal_action": "needs_label" if status != "success" else "none",
        },
    }


def _empty_ledger() -> dict[str, Any]:
    return {"version": 1, "users": {}, "events": []}


def _load_ledger_unlocked() -> dict[str, Any]:
    if not TOKEN_FILE.exists():
        return _empty_ledger()
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_ledger()
    if not isinstance(data, dict):
        return _empty_ledger()
    data.setdefault("version", 1)
    data.setdefault("users", {})
    data.setdefault("events", [])
    return data


def _save_ledger_unlocked(ledger: dict[str, Any]) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = TOKEN_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(TOKEN_FILE)


def _account_unlocked(ledger: dict[str, Any], user: str) -> dict[str, Any]:
    users = ledger.setdefault("users", {})
    if user not in users:
        users[user] = {
            "balance": 0,
            "total_granted": 0,
            "total_used": 0,
            "last_charge": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    return users[user]


def _snapshot_from_account(user: str, account: dict[str, Any]) -> dict[str, Any]:
    return {
        "user": user,
        "balance": int(account.get("balance", 0)),
        "total_granted": int(account.get("total_granted", 0)),
        "total_used": int(account.get("total_used", 0)),
        "last_charge": int(account.get("last_charge", 0)),
        "updated_at": account.get("updated_at"),
        "max_request_tokens": MAX_REQUEST_TOKENS,
    }


def _append_event_unlocked(ledger: dict[str, Any], event: dict[str, Any]) -> None:
    events = ledger.setdefault("events", [])
    events.append({"ts": time.time(), **event})
    del events[:-250]


def _grant_tokens(
    user: str, amount: int, reason: str, grant_id: str | None = None
) -> dict[str, Any]:
    user = _normalize_user(user)
    amount = max(0, int(amount))
    with TOKEN_LOCK:
        ledger = _load_ledger_unlocked()
        if grant_id:
            for event in ledger.get("events", []):
                if (
                    event.get("type") == "grant"
                    and event.get("grant_id") == grant_id
                    and event.get("user") == user
                ):
                    return _snapshot_from_account(user, _account_unlocked(ledger, user))
        account = _account_unlocked(ledger, user)
        account["balance"] = int(account.get("balance", 0)) + amount
        account["total_granted"] = int(account.get("total_granted", 0)) + amount
        account["updated_at"] = time.time()
        _append_event_unlocked(
            ledger,
            {
                "type": "grant",
                "user": user,
                "amount": amount,
                "reason": reason,
                "grant_id": grant_id,
            },
        )
        _save_ledger_unlocked(ledger)
        return _snapshot_from_account(user, account)


def _bootstrap_tokens() -> dict[str, Any]:
    user = _local_tester_id()
    grant_id = f"initial-{user}-5000"
    return _grant_tokens(user, INITIAL_TOKENS, "saldo inicial solicitado", grant_id)


def _token_snapshot(user: str) -> dict[str, Any]:
    user = _normalize_user(user)
    with TOKEN_LOCK:
        ledger = _load_ledger_unlocked()
        account = _account_unlocked(ledger, user)
        _save_ledger_unlocked(ledger)
        return _snapshot_from_account(user, account)


def _ledger_stats() -> dict[str, Any]:
    with TOKEN_LOCK:
        ledger = _load_ledger_unlocked()
    events = ledger.get("events", []) if isinstance(ledger.get("events"), list) else []
    users = ledger.get("users", {}) if isinstance(ledger.get("users"), dict) else {}
    return {
        "users": len(users),
        "events": len(events),
        "grants": sum(1 for event in events if event.get("type") == "grant"),
        "charges": sum(1 for event in events if event.get("type") == "spend"),
        "rejections": sum(1 for event in events if event.get("type") == "reject"),
        "tokens_granted": sum(int(account.get("total_granted", 0)) for account in users.values() if isinstance(account, dict)),
        "tokens_used": sum(int(account.get("total_used", 0)) for account in users.values() if isinstance(account, dict)),
    }


def _runtime_connectors() -> dict[str, Any]:
    connectors = [
        {
            "id": DEFAULT_MODEL_ID,
            "name": "AgentMax Test Server",
            "status": "online",
            "endpoint": "http://127.0.0.1:7790",
            "capabilities": ["chat", "tokens", "checklist", "vision_metadata", "dataset_logging"],
            "risk_level": "low",
        },
        {
            "id": "openai-compatible",
            "name": "AgentMax OpenAI-compatible API",
            "status": "online",
            "endpoint": "http://127.0.0.1:1235/v1",
            "capabilities": ["models", "chat_completions"],
            "risk_level": "low",
        },
        {
            "id": "desktop-tools",
            "name": "Tauri Desktop Tools",
            "status": "requires_desktop_ipc",
            "endpoint": "tauri://local",
            "capabilities": ["screenshot", "mouse_move", "supervised_control"],
            "risk_level": "medium",
        },
        {
            "id": "dataset",
            "name": "AgentMax Dataset Logger",
            "status": "online" if _dataset_settings().get("enabled", False) else "paused",
            "endpoint": str(DATASET_FILE),
            "capabilities": ["redaction", "jsonl_examples", "vision_metadata"],
            "risk_level": "low",
        },
    ]
    return {
        "object": "list",
        "count": len(connectors),
        "data": connectors,
        "connectors": connectors,
        "generated_at": _now_iso(),
    }


def _telemetry_stats() -> dict[str, Any]:
    ledger = _ledger_stats()
    dataset = _dataset_settings()
    return {
        "status": "ok",
        "mode": "local_test",
        "uptime_sec": round(time.time() - STARTED_AT, 1),
        "tasks": {
            "total": len(TASKS),
            "completed": sum(1 for task in TASKS.values() if task.get("status") == "completed"),
            "queued": sum(1 for task in TASKS.values() if task.get("status") in {"queued", "planning", "executing"}),
            "failed": sum(1 for task in TASKS.values() if task.get("status") == "failed"),
        },
        "tokens": ledger,
        "dataset": {
            **dataset,
            "examples": _dataset_count(),
            "path": str(DATASET_FILE),
        },
        "approvals": {
            "pending": 0,
            "approved": 0,
            "denied": 0,
        },
        "generated_at": _now_iso(),
    }


def _pending_approvals() -> dict[str, Any]:
    return {
        "object": "list",
        "count": 0,
        "data": [],
        "approvals": [],
        "status": "ok",
        "message": "No hay aprobaciones pendientes en el servidor local de prueba.",
        "generated_at": _now_iso(),
    }


def _rough_tokens(text: str) -> int:
    clean = text.strip()
    if not clean:
        return 1
    return max(1, (len(clean) + 3) // 4)


def _estimate_charge(prompt: str, completion: str = "") -> int:
    return min(MAX_REQUEST_TOKENS, max(4, _rough_tokens(prompt) + _rough_tokens(completion) + 2))


def _spend_tokens(user: str, amount: int, reason: str) -> tuple[bool, dict[str, Any], str | None]:
    user = _normalize_user(user)
    amount = max(0, int(amount))
    if amount > MAX_REQUEST_TOKENS:
        return False, _token_snapshot(user), "request_too_large"

    with TOKEN_LOCK:
        ledger = _load_ledger_unlocked()
        account = _account_unlocked(ledger, user)
        balance = int(account.get("balance", 0))
        if balance < amount:
            account["last_charge"] = amount
            account["updated_at"] = time.time()
            _append_event_unlocked(
                ledger,
                {
                    "type": "reject",
                    "user": user,
                    "amount": amount,
                    "reason": reason,
                    "balance": balance,
                },
            )
            _save_ledger_unlocked(ledger)
            return False, _snapshot_from_account(user, account), "insufficient_tokens"

        account["balance"] = balance - amount
        account["total_used"] = int(account.get("total_used", 0)) + amount
        account["last_charge"] = amount
        account["updated_at"] = time.time()
        _append_event_unlocked(
            ledger,
            {
                "type": "spend",
                "user": user,
                "amount": amount,
                "reason": reason,
                "balance": account["balance"],
            },
        )
        _save_ledger_unlocked(ledger)
        return True, _snapshot_from_account(user, account), None


def _build_checklist(
    text: str,
    snapshot: dict[str, Any] | None = None,
    blocked: bool = False,
    image_count: int = 0,
) -> list[dict[str, str]]:
    lower = text.lower()
    balance = int((snapshot or {}).get("balance", 1))
    needs_tools = bool(ACTION_RE.search(lower))
    items = [
        {"id": "intent", "label": "Entender la solicitud del usuario", "status": "done"},
        {
            "id": "tokens",
            "label": "Verificar saldo de tokens antes de responder",
            "status": "blocked" if blocked or balance <= 0 else "done",
        },
        {"id": "risk", "label": "Revisar riesgo basico y permisos necesarios", "status": "done"},
    ]
    if image_count:
        items.append(
            {
                "id": "vision",
                "label": f"Detectar {image_count} imagen(es) adjuntas por bytes",
                "status": "done",
            }
        )
    if needs_tools:
        items.extend(
            [
                {
                    "id": "tool_scope",
                    "label": "Detectar si la orden necesita herramientas reales",
                    "status": "done",
                },
                {
                    "id": "permission",
                    "label": "Pedir permiso antes de controlar la PC",
                    "status": "pending",
                },
            ]
        )
    else:
        items.append(
            {
                "id": "direct_response",
                "label": "Responder desde la cabina sin controlar la PC",
                "status": "done",
            }
        )
    items.append(
        {
            "id": "dataset",
            "label": "Registrar ejemplo depurado para dataset local",
            "status": "done" if not blocked else "pending",
        }
    )
    return items


def _checklist_score(items: list[dict[str, str]]) -> float:
    if not items:
        return 0.0
    done = sum(1 for item in items if item.get("status") == "done")
    return round(done / len(items), 2)


def _thinking_core(
    text: str,
    snapshot: dict[str, Any],
    charge: int = 0,
    blocked: bool = False,
    images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    image_meta = images or []
    checklist = _build_checklist(text, snapshot, blocked, len(image_meta))
    return {
        "intent": "vision_metadata" if image_meta else "first_run_test",
        "reasoning_depth": "light",
        "confidence_score": 0.95 if not blocked else 0.62,
        "risk_score": 0.04
        if image_meta
        else (0.02 if not ACTION_RE.search(text.lower()) else 0.18),
        "checklist_score": _checklist_score(checklist),
        "checklist": checklist,
        "token_usage": charge,
        "token_balance": snapshot.get("balance", 0),
        "vision": {"detected": bool(image_meta), "images": image_meta},
        "dataset": {"enabled": _dataset_settings().get("enabled", False), "path": str(DATASET_FILE)},
        "suggested_tools": ["test_server", "local_api", "token_ledger", "vision_metadata"],
    }


def _agent_reply(text: str) -> str:
    clean = text.strip()
    if not clean:
        return "AgentMax esta listo. Escribe una tarea o una pregunta para probar la cabina."

    lower = clean.lower()
    if "estado" in lower or "status" in lower or "health" in lower:
        return "Estoy conectado al runtime local en 7790. Puedo responder en la cabina y coordinar herramientas locales cuando la app de escritorio tenga permiso."
    if "hola" in lower or "hello" in lower:
        return "Listo. Dime que quieres revisar o ejecutar y te pedire permiso si hace falta usar herramientas."
    if ACTION_RE.search(lower):
        return "Esa accion requiere una herramienta local. Si estas en la app de escritorio, te pedire permiso y la ejecutare con el supervisor; si estas solo en el navegador, no puedo controlar el sistema."
    return f'Entendido: "{clean}". Si necesitas diagnostico, pega el error real o dime que evidencia puedo revisar.'


def _image_reply(text: str, images: list[dict[str, Any]]) -> str:
    if not images:
        return _agent_reply(text)
    lines = [
        f"Detecte {len(images)} imagen(es) adjunta(s) y ya registre metadata para el dataset local."
    ]
    for idx, image in enumerate(images, start=1):
        dimensions = (
            f"{image.get('width')}x{image.get('height')}"
            if image.get("width") and image.get("height")
            else "dimensiones no disponibles"
        )
        size_kb = round(int(image.get("bytes", 0)) / 1024, 1)
        lines.append(
            f"{idx}. {image.get('kind', 'imagen').upper()} · {dimensions} · {size_kb} KB · hash {str(image.get('sha256', ''))[:12]}"
        )
    lines.append(
        "Esta deteccion es real por bytes/formato. Para OCR u objetos necesito conectar un modelo visual local o externo."
    )
    return "\n".join(lines)


class AgentMaxTestHandler(BaseHTTPRequestHandler):
    server_version = "AgentMaxServer/0.1.1" if PRODUCT_MODE else "AgentMaxTestServer/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(
            f"[{time.strftime('%H:%M:%S')}][HTTP:{self.server.server_port}] {fmt % args}",
            flush=True,
        )

    def do_OPTIONS(self) -> None:
        _json_response(self, 204, {})

    def do_GET(self) -> None:
        path = _path(self)

        if path in {"/health", "/api/health"}:
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "status": "ok",
                    "version": APP_VERSION,
                    "backend": BACKEND_ID,
                    "ipc_auth_enabled": IPC_AUTH_ENABLED,
                    "tester_id": _local_tester_id(),
                    "runtime_mode": RUNTIME_MODE,
                    "limited": LIMITED_MODE,
                    "fallback_reason": os.environ.get("AGENTMAX_FALLBACK_REASON"),
                    "capabilities": {
                        "chat": True,
                        "vision_metadata": True,
                        "screen_vision": BETA_CONFIG.feature_flags.screen_vision,
                        "mouse_control": False,
                        "keyboard_control": False,
                        "terminal": False,
                        "file_actions": False,
                    },
                },
            )
            return
        if not _ipc_authorized(self, path):
            return

        if path == "/api/tokens":
            user = _request_user(self)
            _json_response(self, 200, {"tokens": _token_snapshot(user)})
            return

        if path == "/api/beta/status":
            _json_response(self, 200, _beta_status_payload())
            return

        if path == "/api/beta/config":
            _json_response(self, 200, BETA_CONFIG.safe_dict)
            return

        if path == "/api/tasks/recent":
            limit = (_query(self).get("limit") or ["20"])[0]
            try:
                task_limit = int(limit)
            except ValueError:
                task_limit = 20
            _json_response(self, 200, {"tasks": BETA_STORAGE.recent_tasks(task_limit)})
            return

        if path == "/api/status":
            _json_response(
                self,
                200,
                {
                    "status": "running",
                    "timestamp": time.time(),
                    "uptime_sec": round(time.time() - STARTED_AT, 1),
                    "tokens": _token_snapshot(_local_tester_id()),
                    "dataset": {
                        **_dataset_settings(),
                        "examples": _dataset_count(),
                    },
                    "beta": {
                        "version": BETA_CONFIG.app_version,
                        "environment": BETA_CONFIG.app_env,
                        "sqlite": BETA_STORAGE.status(),
                        "redis": BETA_REDIS.status().__dict__,
                        "feature_flags": BETA_CONFIG.safe_dict["feature_flags"],
                    },
                    "agents": {
                        "pilot": {
                            "name": "pilot",
                            "state": "ready",
                            "actions": len(TASKS),
                            "errors": 0,
                            "avg_latency_ms": 12.0,
                        },
                        "operator": {
                            "name": "operator",
                            "state": "test_mode",
                            "actions": 0,
                            "errors": 0,
                            "avg_latency_ms": 0.0,
                        },
                    },
                    "bus_depth": 0,
                    "ipc_metrics": {
                        "request_count": len(TASKS),
                        "error_count": 0,
                        "avg_latency_ms": 12.0,
                    },
                },
            )
            return

        if path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            _json_response(
                self,
                200,
                TASKS.get(
                    task_id,
                    {
                        "task_id": task_id,
                        "status": "completed",
                        "steps_completed": 1,
                        "result": "Tarea de prueba completada.",
                    },
                ),
            )
            return

        if path == "/api/tools":
            _json_response(
                self,
                200,
                {
                    "catalog": {
                        "count": 3,
                        "valid": True,
                        "categories": {
                            "test": [
                                {
                                    "id": "chat",
                                    "name": "Chat",
                                    "enabled": True,
                                    "risk_level": "low",
                                },
                                {
                                    "id": "status",
                                    "name": "Status",
                                    "enabled": True,
                                    "risk_level": "low",
                                },
                                {
                                    "id": "tasks",
                                    "name": "Tasks",
                                    "enabled": True,
                                    "risk_level": "low",
                                },
                                {
                                    "id": "vision_metadata",
                                    "name": "Vision Metadata",
                                    "enabled": True,
                                    "risk_level": "low",
                                },
                                {
                                    "id": "training_dataset",
                                    "name": "Training Dataset",
                                    "enabled": True,
                                    "risk_level": "medium",
                                },
                            ],
                        },
                    },
                },
            )
            return

        if path in {"/v1/connectors", "/api/connectors"}:
            _json_response(self, 200, _runtime_connectors())
            return

        if path in {"/v1/telemetry/stats", "/api/telemetry/stats"}:
            _json_response(self, 200, _telemetry_stats())
            return

        if path in {"/v1/approvals/pending", "/api/approvals/pending"}:
            _json_response(self, 200, _pending_approvals())
            return

        if path == "/api/ai/status":
            _json_response(
                self,
                200,
                {
                    "backend": "test",
                    "model": VISION_MODEL_ID,
                    "supports_vision": True,
                    "vision_mode": "metadata",
                    "health": {
                        "ok": True,
                        "models": [DEFAULT_MODEL_ID, VISION_MODEL_ID],
                    },
                },
            )
            return

        if path == "/api/dataset/status":
            _json_response(
                self,
                200,
                {
                    "dataset": {
                        **_dataset_settings(),
                        "examples": _dataset_count(),
                        "exists": DATASET_FILE.exists(),
                    },
                },
            )
            return

        if path in {"/v1/models", "/models"}:
            _json_response(
                self,
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": DEFAULT_MODEL_ID, "object": "model", "owned_by": "local"},
                        {
                            "id": VISION_MODEL_ID,
                            "object": "model",
                            "owned_by": "local",
                        },
                    ],
                },
            )
            return

        _json_response(self, 404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:
        path = _path(self)
        if not _ipc_authorized(self, path):
            return
        data = _read_json(self)

        if path == "/api/feedback":
            user = BETA_STORAGE.ensure_user(user_id=_request_user(self, data))
            rating_raw = data.get("rating")
            try:
                rating = int(rating_raw) if rating_raw is not None else None
            except (TypeError, ValueError):
                rating = None
            feedback_id = BETA_STORAGE.save_feedback(
                user_id=user,
                conversation_id=str(data.get("conversation_id") or "") or None,
                task_id=str(data.get("task_id") or "") or None,
                rating=rating,
                category=str(data.get("category") or "other"),
                message=str(data.get("message") or ""),
                screenshot_path=str(data.get("screenshot_path") or "") or None,
                logs_path=str(data.get("logs_path") or "") or None,
            )
            BETA_LOGGER.log(
                "beta_feedback",
                level="info",
                source="api",
                event="feedback.saved",
                metadata={"feedback_id": feedback_id, "category": data.get("category")},
            )
            _json_response(self, 200, {"ok": True, "feedback_id": feedback_id})
            return

        if path == "/api/diagnostics/export":
            result = export_diagnostics_bundle(storage=BETA_STORAGE)
            BETA_LOGGER.log(
                "app",
                level="info",
                source="api",
                event="diagnostics.exported",
                metadata={"zip_path": result.get("zip_path")},
            )
            _json_response(self, 200, result)
            return

        if path == "/api/beta/smoke-test":
            result = run_beta_smoke_test()
            BETA_LOGGER.log(
                "app",
                level="info" if result.get("ok") else "error",
                source="api",
                event="beta.smoke_test",
                metadata={"checks": result.get("checks")},
            )
            _json_response(self, 200 if result.get("ok") else 500, result)
            return

        if path == "/api/beta/tester-access":
            user = BETA_STORAGE.ensure_user(user_id=_request_user(self, data))
            enabled = str(data.get("enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
            BETA_STORAGE.set_setting("tester.full_agentpilot_access", enabled, user_id=user)
            BETA_STORAGE.add_agent_event(
                task_id=None,
                event_type="tester.full_agentpilot_access",
                payload={"user": user, "enabled": enabled, "control_consent": "per_action"},
            )
            BETA_LOGGER.log(
                "app",
                level="info",
                source="api",
                event="tester.full_agentpilot_access",
                metadata={"enabled": enabled, "control_consent": "per_action"},
            )
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "enabled": enabled,
                    "access": "full" if enabled else "standard",
                    "control_consent": "per_action",
                },
            )
            return

        if path == "/api/tokens/grant":
            if not _admin_authorized(self, data):
                _json_response(
                    self,
                    403,
                    {
                        "error": "admin_required",
                        "message": "Define AGENTMAX_ADMIN_KEY y envia X-AgentMax-Admin para hacer recargas manuales.",
                    },
                )
                return
            user = _request_user(self, data)
            try:
                amount = int(data.get("amount", 0) or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                _json_response(
                    self,
                    400,
                    {"error": "invalid_amount", "message": "amount debe ser mayor que cero"},
                )
                return
            _json_response(
                self,
                200,
                {
                    "tokens": _grant_tokens(
                        user, amount, str(data.get("reason") or "manual_grant")
                    ),
                },
            )
            return

        if path == "/api/dataset/settings":
            current = _dataset_settings()
            next_settings = {
                **current,
                "enabled": data.get("enabled", current.get("enabled", True)),
                "store_images": data.get("store_images", current.get("store_images", False)),
                "redact_text": data.get("redact_text", current.get("redact_text", True)),
            }
            _json_response(
                self,
                200,
                {
                    "dataset": {
                        **_save_dataset_settings(next_settings),
                        "examples": _dataset_count(),
                    }
                },
            )
            return

        if path == "/api/dataset/event":
            user = _request_user(self, data)
            prompt = str(data.get("prompt") or data.get("message") or "")
            reply = str(data.get("reply") or "")
            error = str(data.get("error") or "") or None
            images = _extract_images(data)
            record = _training_record(
                kind=str(data.get("kind") or "client_event"),
                user=user,
                prompt=prompt,
                reply=reply,
                status=str(data.get("status") or ("failed" if error else "success")),
                error=error,
                thinking_core=data.get("thinking_core")
                if isinstance(data.get("thinking_core"), dict)
                else {},
                usage=data.get("usage") if isinstance(data.get("usage"), dict) else {},
                tokens=_token_snapshot(user),
                images=images,
            )
            saved = _append_dataset_example(record)
            _json_response(
                self,
                200,
                {
                    "saved": saved,
                    "dataset": {"examples": _dataset_count(), "path": str(DATASET_FILE)},
                },
            )
            return

        if path == "/api/checklist":
            message = str(data.get("message", "")).strip()
            user = _request_user(self, data)
            snapshot = _token_snapshot(user)
            images = _extract_images(data)
            reply = _image_reply(message, images)
            charge = _estimate_charge(message, reply)
            checklist = _build_checklist(
                message, snapshot, snapshot["balance"] < charge, len(images)
            )
            _json_response(
                self,
                200,
                {
                    "checklist": checklist,
                    "checklist_score": _checklist_score(checklist),
                    "estimated_tokens": charge,
                    "tokens": snapshot,
                    "images": images,
                },
            )
            return

        if path == "/api/chat":
            message = str(data.get("message", "")).strip()
            user = _request_user(self, data)
            beta_user = BETA_STORAGE.ensure_user(user_id=user)
            conversation_id = BETA_STORAGE.create_conversation(beta_user, "API chat")
            BETA_STORAGE.add_message(
                conversation_id,
                "user",
                message,
                metadata={"image_count": len(_extract_images(data))},
            )
            images = _extract_images(data)
            reply = _image_reply(message, images)
            charge = _estimate_charge(message, reply)
            ok, snapshot, error = _spend_tokens(user, charge, "chat")
            if not ok:
                thinking_core = _thinking_core(
                    message, snapshot, charge, blocked=True, images=images
                )
                _append_dataset_example(
                    _training_record(
                        kind="chat",
                        user=user,
                        prompt=message,
                        reply="",
                        status="blocked",
                        error=error,
                        thinking_core=thinking_core,
                        usage={"total_tokens": charge},
                        tokens=snapshot,
                        images=images,
                    )
                )
                BETA_STORAGE.record_error(
                    severity="warn",
                    source="api.chat",
                    message=error or "token spend failed",
                    metadata={"user": user, "charge": charge},
                )
                _json_response(
                    self,
                    402,
                    {
                        "error": error,
                        "message": "No hay tokens suficientes para completar esta solicitud.",
                        "tokens": snapshot,
                        "thinking_core": thinking_core,
                    },
                )
                return
            usage = {
                "prompt_tokens": _rough_tokens(message),
                "completion_tokens": _rough_tokens(reply),
                "total_tokens": charge,
            }
            thinking_core = _thinking_core(message, snapshot, charge, images=images)
            BETA_STORAGE.add_message(
                conversation_id,
                "assistant",
                reply,
                metadata={"usage": usage, "thinking_core": thinking_core},
            )
            BETA_LOGGER.log(
                "agent",
                level="info",
                source="api.chat",
                event="chat.completed",
                metadata={"user": user, "charge": charge, "image_count": len(images)},
            )
            _append_dataset_example(
                _training_record(
                    kind="chat",
                    user=user,
                    prompt=message,
                    reply=reply,
                    status="success",
                    thinking_core=thinking_core,
                    usage=usage,
                    tokens=snapshot,
                    images=images,
                )
            )
            _json_response(
                self,
                200,
                {
                    "reply": reply,
                    "task_id": None,
                    "tokens": snapshot,
                    "usage": usage,
                    "images": images,
                    "thinking_core": thinking_core,
                },
            )
            return

        if path == "/api/tasks":
            description = str(data.get("description", "")).strip()
            user = _request_user(self, data)
            beta_user = BETA_STORAGE.ensure_user(user_id=user)
            images = _extract_images(data)
            charge = max(12, _estimate_charge(description, "tarea registrada"))
            ok, snapshot, error = _spend_tokens(user, charge, "task")
            if not ok:
                thinking_core = _thinking_core(
                    description, snapshot, charge, blocked=True, images=images
                )
                _append_dataset_example(
                    _training_record(
                        kind="task",
                        user=user,
                        prompt=description,
                        status="blocked",
                        error=error,
                        thinking_core=thinking_core,
                        usage={"total_tokens": charge},
                        tokens=snapshot,
                        images=images,
                    )
                )
                failed_task_id = BETA_STORAGE.create_task(
                    user_id=beta_user,
                    goal=description or "blocked empty task",
                    status="failed",
                )
                BETA_STORAGE.update_task_status(
                    failed_task_id,
                    "failed",
                    error=error or "insufficient_tokens",
                    result={"charge": charge},
                )
                _json_response(
                    self,
                    402,
                    {
                        "error": error,
                        "message": "No hay tokens suficientes para registrar la tarea.",
                        "tokens": snapshot,
                        "thinking_core": thinking_core,
                    },
                )
                return
            task_id = BETA_STORAGE.create_task(
                user_id=beta_user,
                goal=description or "test task",
                status="queued",
            )
            BETA_STORAGE.update_task_status(task_id, "running", result={"mode": "test_server"})
            thinking_core = _thinking_core(description, snapshot, charge, images=images)
            TASKS[task_id] = {
                "task_id": task_id,
                "id": task_id,
                "description": description,
                "status": "completed",
                "steps_completed": 1,
                "steps_total": 1,
                "result": TASK_COMPLETED_MSG,
                "started_at": time.time(),
                "completed_at": time.time(),
                "checklist": _build_checklist(description, snapshot, image_count=len(images)),
                "token_usage": charge,
                "tokens_remaining": snapshot["balance"],
                "images": images,
            }
            BETA_STORAGE.update_task_status(
                task_id,
                "completed",
                result={
                    "summary": TASK_COMPLETED_MSG,
                    "token_usage": charge,
                    "image_count": len(images),
                },
            )
            BETA_LOGGER.log(
                "agent",
                level="info",
                source="api.tasks",
                event="task.completed",
                metadata={"task_id": task_id, "user": user, "charge": charge},
            )
            _append_dataset_example(
                _training_record(
                    kind="task",
                    user=user,
                    prompt=description,
                    reply=TASK_COMPLETED_MSG,
                    status="success",
                    thinking_core=thinking_core,
                    usage={"total_tokens": charge},
                    tokens=snapshot,
                    images=images,
                )
            )
            _json_response(
                self,
                200,
                {
                    "task_id": task_id,
                    "status": "completed",
                    "tokens": snapshot,
                    "thinking_core": thinking_core,
                },
            )
            return

        if path in {"/api/emergency_stop", "/api/chat/stop"}:
            stopped_ids: list[str] = []
            for task in TASKS.values():
                if task.get("status") in {"queued", "planning", "executing", "running"}:
                    task["status"] = "stopped"
                    task["completed_at"] = time.time()
                    stopped_ids.append(str(task.get("task_id") or task.get("id")))
            for task in BETA_STORAGE.recent_tasks(100):
                if task.get("status") in {"queued", "running", "paused"}:
                    BETA_STORAGE.update_task_status(
                        str(task["id"]),
                        "stopped",
                        result={"reason": "emergency_stop"},
                    )
                    stopped_ids.append(str(task["id"]))
            BETA_STORAGE.add_agent_event(
                task_id=None,
                event_type="emergency_stop",
                payload={"path": path, "stopped_ids": sorted(set(stopped_ids))},
            )
            BETA_LOGGER.log(
                "tools",
                level="warn",
                source="api",
                event="emergency_stop",
                metadata={"path": path, "stopped_ids": sorted(set(stopped_ids))},
            )
            _json_response(self, 200, {"stopped": True, "stopped_ids": sorted(set(stopped_ids))})
            return

        if path in {"/v1/chat/completions", "/chat/completions", "/api/lm/chat"}:
            messages = data.get("messages") if isinstance(data.get("messages"), list) else []
            text = _latest_user_message(messages)
            images = _extract_images(data) or _images_from_openai_messages(messages)
            content = _image_reply(text, images)
            user = _request_user(self, data)
            charge = _estimate_charge(text, content)
            ok, snapshot, error = _spend_tokens(user, charge, "chat_completions")
            if not ok:
                _append_dataset_example(
                    _training_record(
                        kind="chat_completions",
                        user=user,
                        prompt=text,
                        status="blocked",
                        error=error,
                        thinking_core=_thinking_core(
                            text, snapshot, charge, blocked=True, images=images
                        ),
                        usage={"total_tokens": charge},
                        tokens=snapshot,
                        images=images,
                    )
                )
                _json_response(
                    self,
                    402,
                    {
                        "error": {
                            "message": "No hay tokens suficientes para completar esta solicitud.",
                            "type": error,
                            "code": error,
                        },
                        "tokens": snapshot,
                    },
                )
                return
            usage = {
                "prompt_tokens": _rough_tokens(text),
                "completion_tokens": _rough_tokens(content),
                "total_tokens": charge,
            }
            _append_dataset_example(
                _training_record(
                    kind="chat_completions",
                    user=user,
                    prompt=text,
                    reply=content,
                    status="success",
                    thinking_core=_thinking_core(text, snapshot, charge, images=images),
                    usage=usage,
                    tokens=snapshot,
                    images=images,
                )
            )
            _json_response(
                self,
                200,
                {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": data.get("model") or DEFAULT_MODEL_ID,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                    "tokens": snapshot,
                    "images": images,
                },
            )
            return

        _json_response(self, 404, {"error": "not_found", "path": path})


def _serve(port: int, host: str) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), AgentMaxTestHandler)
    thread = threading.Thread(
        target=server.serve_forever, name=f"AgentMax-{port}", daemon=True
    )
    thread.start()
    label = "AgentMax server" if PRODUCT_MODE else "AgentMax test server"
    print(f"[OK] {label}: http://{host}:{port}", flush=True)
    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=BETA_CONFIG.api_port)
    parser.add_argument("--lm-port", type=int, default=BETA_CONFIG.lms_port)
    args = parser.parse_args()

    initial_snapshot = _bootstrap_tokens()
    dataset_snapshot = _save_dataset_settings(_dataset_settings())
    servers = [_serve(args.api_port, args.host), _serve(args.lm_port, args.host)]
    stop = threading.Event()

    def _stop(*_: Any) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    print(
        f"[OK] Tokens iniciales para {initial_snapshot['user']}: {initial_snapshot['balance']}",
        flush=True,
    )
    print(
        f"[OK] Dataset local: {dataset_snapshot['path']} (enabled={dataset_snapshot['enabled']}, store_images={dataset_snapshot['store_images']})",
        flush=True,
    )
    print(
        f"[OK] AgentMax server mode={RUNTIME_MODE} backend={BACKEND_ID} limited={LIMITED_MODE}",
        flush=True,
    )
    stop.wait()

    for server in servers:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    if not os.environ.get("AGENTMAX_SERVER_WRAPPER"):
        print(
            "[DEPRECATED] Use scripts/agentmax_beta_server.py or "
            "scripts/agentmax_dev_server.py explicitly.",
            flush=True,
        )
    raise SystemExit(main())
