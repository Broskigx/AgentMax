from __future__ import annotations

import json
import zipfile

import pytest

from core.beta.config import BetaConfig, load_beta_config
from core.beta.diagnostics import export_diagnostics_bundle
from core.beta.logging_service import BetaLogger
from core.beta.redis_service import RedisService
from core.beta.storage import StorageService


def _config(tmp_path) -> BetaConfig:
    return BetaConfig(sqlite_path=str(tmp_path / "agentmax_beta.sqlite"))


def test_global_settings_overwrite_with_null_user_fallback(tmp_path) -> None:
    storage = StorageService(config=_config(tmp_path))

    storage.set_setting("redis_fallback.queue.qa", [{"id": "old"}])
    storage.set_setting("redis_fallback.queue.qa", [{"id": "new"}])

    assert storage.get_setting("redis_fallback.queue.qa") == [{"id": "new"}]
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT key,value_json FROM settings WHERE key='redis_fallback.queue.qa'"
        ).fetchall()
    assert len(rows) == 1


def test_redis_degraded_queue_uses_sqlite_once(tmp_path) -> None:
    storage = StorageService(config=_config(tmp_path))
    redis = RedisService("redis://127.0.0.1:1/0", config=_config(tmp_path), storage=storage)

    status = redis.connect()
    redis.enqueueTask("qa", {"id": "task-1"})

    assert status.degraded is True
    assert redis.dequeueTask("qa") == {"id": "task-1"}
    assert redis.dequeueTask("qa") is None


def test_redis_heartbeat_reports_degraded_without_crashing(tmp_path) -> None:
    storage = StorageService(config=_config(tmp_path))
    redis = RedisService("redis://127.0.0.1:1/0", config=_config(tmp_path), storage=storage)

    status = redis.heartbeat()

    assert status.available is False
    assert status.degraded is True


def test_storage_rejects_secret_like_setting_keys(tmp_path) -> None:
    storage = StorageService(config=_config(tmp_path))

    with pytest.raises(ValueError):
        storage.set_setting("api_key", "secret")


def test_diagnostics_bundle_redacts_logs_and_config(tmp_path) -> None:
    cfg = _config(tmp_path)
    cfg.sentry_dsn = "https://public:private@example.invalid/1"
    storage = StorageService(config=cfg)
    logger = BetaLogger(log_dir=tmp_path / "logs", storage=storage)
    logger.log(
        "app",
        level="info",
        source="qa",
        event="redaction.test",
        metadata={"token": "abc123456789", "email": "tester@example.com"},
    )

    result = export_diagnostics_bundle(tmp_path / "diag", config=cfg, storage=storage)

    assert result["ok"] is True
    with zipfile.ZipFile(result["zip_path"]) as zf:
        report = json.loads(zf.read("diagnostics_report.json").decode("utf-8"))
    assert report["config"]["sentry_dsn"] == "<SECRET>"
    assert "private@example.invalid" not in json.dumps(report)


def test_invalid_beta_config_is_reported_not_silent(tmp_path) -> None:
    (tmp_path / "agentmax.config.json").write_text("{ bad json", encoding="utf-8")

    cfg = load_beta_config(tmp_path)

    assert cfg.config_errors
    assert "invalid JSON" in cfg.config_errors[0]


def test_env_overrides_json_config(monkeypatch, tmp_path) -> None:
    (tmp_path / "agentmax.config.json").write_text(
        '{"app_env":"json-env","api_port":1234,"feature_flags":{"telemetry":true}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTMAX_APP_ENV", "env-env")
    monkeypatch.setenv("AGENTMAX_API_PORT", "7799")
    monkeypatch.setenv("AGENTMAX_FEATURE_TELEMETRY", "0")

    cfg = load_beta_config(tmp_path)

    assert cfg.app_env == "env-env"
    assert cfg.api_port == 7799
    assert cfg.feature_flags.telemetry is False
