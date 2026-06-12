"""Beta REST routes exposed through the runtime IPC bridge."""

import asyncio

from core.beta import rest_api
from core.ipc import IPC_API_VERSION, IPCServer


def _bare_server() -> IPCServer:
    server = IPCServer.__new__(IPCServer)
    server._metrics = {
        "request_count": 0,
        "error_count": 0,
        "avg_latency_ms": 0.0,
        "auth_rejected": 0,
    }
    server._agent_pool = {}
    server._bus = object()
    server._ipc_auth_enabled = False
    return server


def _rest(method: str, path: str, data: dict | None = None) -> dict:
    return asyncio.run(_bare_server().handle_rest(method, path, data))


def test_health_matches_ui_contract():
    body = _rest("GET", "/health")
    assert body["ok"] is True
    assert body["api_version"] == IPC_API_VERSION
    assert body["backend"] == "agentmax"
    assert body["version"] == "0.1.1"
    assert body["ipc_auth_enabled"] is False
    assert body["runtime_mode"] == "full"
    assert body["limited"] is False
    assert body["fallback_reason"] is None
    assert "capabilities" in body
    assert body["tester_id"]
    assert "agust" not in body["tester_id"].lower()


def test_health_reports_limited_fallback_explicitly(monkeypatch):
    monkeypatch.setattr(rest_api, "RUNTIME_MODE", "beta_fallback")
    monkeypatch.setattr(rest_api, "BACKEND_ID", "agentmax_beta_fallback")
    monkeypatch.setattr(rest_api, "APP_VERSION", "0.1.1-limited")
    monkeypatch.setenv("AGENTMAX_FALLBACK_REASON", "dependency unavailable")

    body = _rest("GET", "/health")

    assert body["backend"] == "agentmax_beta_fallback"
    assert body["runtime_mode"] == "beta_fallback"
    assert body["limited"] is True
    assert body["fallback_reason"] == "dependency unavailable"


def test_beta_config_route():
    body = _rest("GET", "/api/beta/config")
    assert "feature_flags" in body
    assert "api_port" in body


def test_diagnostics_export_route():
    body = _rest("POST", "/api/diagnostics/export", {})
    assert body.get("zip_path") or body.get("folder")
