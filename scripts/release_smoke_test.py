#!/usr/bin/env python3
"""Release smoke test for AgentMax closed beta (HTTP + config hygiene)."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_BASE = "http://127.0.0.1:7790"
PERSONAL_PATH_RE = re.compile(r"(?i)C:\\Users\\agust")
HARDCODED_USER_RE = re.compile(r"\bagust\b")
AUTH_TOKEN: str | None = None


def _get(path: str) -> tuple[int, dict]:
    req = urllib.request.Request(f"{API_BASE}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 — local beta probe
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(body) if body else {}


def _post(path: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if AUTH_TOKEN:
        headers["X-AgentMax-Token"] = AUTH_TOKEN
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(body) if body else {}


def main() -> int:
    global AUTH_TOKEN
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}

    try:
        status, health = _get("/health")
        checks["health_ok"] = status == 200 and bool(health.get("ok"))
        details["health"] = health
        tester_id = str(health.get("tester_id", ""))
        checks["no_hardcoded_agust_user"] = "agust" not in tester_id.lower()
        expected_mode = os.environ.get("AGENTMAX_EXPECT_RUNTIME_MODE", "full")
        checks["runtime_mode"] = health.get("runtime_mode") == expected_mode
        if expected_mode == "full":
            checks["product_backend_id"] = health.get("backend") == "agentmax"
            checks["runtime_not_limited"] = health.get("limited") is False
        else:
            checks["product_backend_id"] = health.get("backend") == f"agentmax_{expected_mode}"
            checks["runtime_not_limited"] = health.get("limited") is True
        checks["product_version"] = health.get("version") not in (None, "")
        details["tester_id"] = tester_id
        if health.get("ipc_auth_enabled"):
            AUTH_TOKEN = os.environ.get("AGENTMAX_IPC_TOKEN", "").strip() or None
            if not AUTH_TOKEN:
                local_app_data = Path(
                    os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
                )
                token_path = local_app_data / "AgentMax" / "ipc_token"
                AUTH_TOKEN = (
                    token_path.read_text(encoding="utf-8").strip()
                    if token_path.exists()
                    else None
                )
            checks["ipc_token_available"] = bool(AUTH_TOKEN)

            unauthorized = urllib.request.Request(f"{API_BASE}/api/status", method="GET")
            try:
                urllib.request.urlopen(unauthorized, timeout=8)  # noqa: S310
                checks["ipc_rejects_missing_token"] = False
            except urllib.error.HTTPError as exc:
                checks["ipc_rejects_missing_token"] = exc.code == 401
        else:
            checks["ipc_token_available"] = False
            checks["ipc_rejects_missing_token"] = False
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        checks["health_ok"] = False
        details["health_error"] = str(exc)
        print(json.dumps({"ok": False, "checks": checks, "details": details}, indent=2))
        return 1

    try:
        status, task = _post("/api/tasks", {"description": "smoke: listar directorio actual"})
        checks["task_create_ok"] = status == 200 and bool(task.get("task_id") or task.get("status"))
        details["task"] = task
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        checks["task_create_ok"] = False
        details["task_error"] = str(exc)

    try:
        status, diag = _post("/api/diagnostics/export", {})
        checks["diagnostics_export_ok"] = status == 200 and bool(
            diag.get("zip_path") or diag.get("folder")
        )
        details["diagnostics"] = diag
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        checks["diagnostics_export_ok"] = False
        details["diagnostics_error"] = str(exc)

    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    logs_dir = local_app_data / "AgentMax" / "logs"
    checks["logs_dir_exists"] = logs_dir.exists()
    if logs_dir.exists():
        log_files = list(logs_dir.glob("*.log")) + list(logs_dir.glob("*.jsonl"))
        checks["logs_present"] = len(log_files) > 0
        details["log_files"] = [p.name for p in log_files[:10]]
    else:
        checks["logs_present"] = False

    models_json = ROOT / "models" / "AgentPilot" / "models.json"
    if models_json.exists():
        raw = models_json.read_text(encoding="utf-8")
        checks["models_no_personal_paths"] = PERSONAL_PATH_RE.search(raw) is None
    else:
        checks["models_no_personal_paths"] = True

    agentmax_cfg = ROOT / "agentmax.config.json"
    if agentmax_cfg.exists():
        raw_cfg = agentmax_cfg.read_text(encoding="utf-8")
        checks["config_no_personal_paths"] = PERSONAL_PATH_RE.search(raw_cfg) is None
    else:
        checks["config_no_personal_paths"] = False

    health_blob = json.dumps(details.get("health", {}))
    checks["health_blob_no_agust"] = HARDCODED_USER_RE.search(health_blob) is None

    ok = all(checks.values())
    print(json.dumps({"ok": ok, "checks": checks, "details": details}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
