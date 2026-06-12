import json

import pytest

from core.security import ipc_auth


def test_rest_auth_rejects_missing_or_invalid_token():
    with pytest.raises(ipc_auth.IPCAuthError):
        ipc_auth.check_rest_request(
            path="/api/status",
            headers={},
            enabled=True,
            expected_token="secret-token",
        )

    with pytest.raises(ipc_auth.IPCAuthError):
        ipc_auth.check_rest_request(
            path="/api/status",
            headers={ipc_auth.AUTH_HEADER: "wrong-token"},
            enabled=True,
            expected_token="secret-token",
        )


def test_rest_auth_accepts_token_and_health_exemptions():
    ipc_auth.check_rest_request(
        path="/api/status",
        headers={"X-Agentmax-Token": "secret-token"},
        enabled=True,
        expected_token="secret-token",
    )
    for path in ("/health", "/api/health"):
        ipc_auth.check_rest_request(
            path=path,
            headers={},
            enabled=True,
            expected_token="secret-token",
        )


def test_environment_token_is_persisted_for_desktop_clients(tmp_path, monkeypatch):
    token_path = tmp_path / "ipc_token"
    monkeypatch.setenv(ipc_auth.TOKEN_ENV_VAR, "environment-token")
    monkeypatch.setattr(ipc_auth, "token_file_path", lambda: token_path)

    assert ipc_auth.ensure_token() == "environment-token"
    assert token_path.read_text(encoding="utf-8") == "environment-token"


def test_auth_flag_uses_secure_config_default(monkeypatch):
    monkeypatch.delenv(ipc_auth.AUTH_FLAG_ENV, raising=False)
    config = type("Config", (), {"ipc_auth_enabled": True})()
    assert ipc_auth.is_auth_enabled(config) is True

    monkeypatch.setenv(ipc_auth.AUTH_FLAG_ENV, "0")
    assert ipc_auth.is_auth_enabled(config) is False


class FakeWebSocket:
    def __init__(self, message: dict):
        self.message = json.dumps(message)
        self.sent: list[dict] = []
        self.closed_with: int | None = None

    async def recv(self):
        return self.message

    async def send(self, payload: str):
        self.sent.append(json.loads(payload))

    async def close(self, code: int):
        self.closed_with = code


@pytest.mark.asyncio
async def test_websocket_requires_valid_first_message():
    valid = FakeWebSocket({"cmd": "auth", "token": "secret-token"})
    assert await ipc_auth.authenticate_ws(
        valid,
        expected_token="secret-token",
        enabled=True,
    )
    assert valid.sent[0]["type"] == "auth_ok"

    invalid = FakeWebSocket({"cmd": "auth", "token": "wrong-token"})
    assert not await ipc_auth.authenticate_ws(
        invalid,
        expected_token="secret-token",
        enabled=True,
    )
    assert invalid.sent[0]["type"] == "auth_failed"
    assert invalid.closed_with == 4401
