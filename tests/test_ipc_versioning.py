"""Tests for the IPC API version surface (REST routes + constant).

The version is exposed non-breakingly on /api/version, /api/status and /health
so clients (CLI, Tauri UI) can detect contract compatibility. These tests drive
``handle_rest`` directly on a bare IPCServer — bypassing ``__init__`` (which
spawns AgentCoreProxy and writes a token file) by setting only the few
attributes the version routes touch.
"""

import asyncio

from core.ipc import IPC_API_VERSION, IPCServer


def _bare_server() -> IPCServer:
    """An IPCServer with just the attributes the version routes read."""
    server = IPCServer.__new__(IPCServer)
    server._metrics = {
        "request_count": 0,
        "error_count": 0,
        "avg_latency_ms": 0.0,
        "auth_rejected": 0,
    }
    server._agent_pool = {}
    server._bus = object()  # getattr(..., "queue_depth", 0) falls back to 0
    return server


def _rest(path: str) -> dict:
    return asyncio.run(_bare_server().handle_rest("GET", path))


def test_version_constant_is_semver_like():
    assert isinstance(IPC_API_VERSION, str)
    assert IPC_API_VERSION
    # major.minor shape, all numeric components
    parts = IPC_API_VERSION.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts)


def test_api_version_endpoint():
    assert _rest("/api/version") == {"api_version": IPC_API_VERSION}


def test_health_reports_api_version():
    server = _bare_server()
    server._ipc_auth_enabled = False
    body = asyncio.run(server.handle_rest("GET", "/health"))
    assert body["ok"] is True
    assert body["api_version"] == IPC_API_VERSION
    assert body["backend"] == "agentmax"
    assert "tester_id" in body


def test_status_includes_api_version():
    body = _rest("/api/status")
    assert body["status"] == "running"
    assert body["api_version"] == IPC_API_VERSION
