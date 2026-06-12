"""Closed beta smoke checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import get_beta_config
from .diagnostics import export_diagnostics_bundle
from .logging_service import BetaLogger
from .redis_service import RedisService
from .storage import StorageService


def run_beta_smoke_test(tmp_dir: str | Path | None = None) -> dict[str, Any]:
    cfg = get_beta_config()
    storage = StorageService(config=cfg)
    storage.migrate()
    logger = BetaLogger(storage=storage)
    redis = RedisService(config=cfg, storage=storage)
    redis_status = redis.connect()

    user = storage.ensure_user(user_id="smoke_tester", display_name="Smoke Tester")
    task_id = storage.create_task(user_id=user, goal="closed beta smoke task")
    storage.update_task_status(task_id, "running")
    storage.update_task_status(task_id, "paused", result={"reason": "manual_pause"})
    redis.setTaskState(task_id, {"status": "paused", "reason": "redis_fallback_smoke"})
    redis.enqueueTask("smoke", {"id": task_id})
    dequeued = redis.dequeueTask("smoke")
    rate_ok = redis.rateLimitCheck("smoke", limit=2, window_sec=60)
    storage.update_task_status(task_id, "stopped", result={"reason": "smoke_complete"})
    feedback_id = storage.save_feedback(
        user_id=user,
        task_id=task_id,
        rating=5,
        category="bug",
        message="Smoke feedback with token=should_redact",
    )
    logger.log("app", level="info", source="smoke", event="smoke.started", metadata={"task": task_id})
    logger.log("beta_feedback", level="info", source="smoke", event="feedback.saved", metadata={"feedback_id": feedback_id})
    diagnostics = export_diagnostics_bundle(output_dir=tmp_dir, config=cfg, storage=storage)
    feedback_json = " ".join(str(row) for row in storage.feedback_rows()[:10])
    app_logs = " ".join(str(row) for row in logger.recent("app", 10))

    flags = cfg.safe_dict["feature_flags"]
    expected_flags = {
        "sqlite_storage": True,
        "screen_vision": True,
        "local_agentpilot": True,
        "telemetry": False,
    }
    flags_ok = all(flags.get(key) is value for key, value in expected_flags.items())

    checks = {
        "config_loads": True,
        "sqlite_tables": bool(storage.status()["tables"]),
        "logs_write": bool(logger.recent("app", 5)),
        "feedback_saves": bool(feedback_id),
        "redis_fallback_or_connected": redis_status.available or redis_status.degraded,
        "queue_round_trip": bool(dequeued and dequeued.get("id") == task_id),
        "rate_limit": rate_ok,
        "task_states": storage.recent_tasks(1)[0]["status"] == "stopped",
        "feature_flags": flags_ok,
        "diagnostics": bool(diagnostics.get("zip_path")),
        "no_visible_secrets": "should_redact" not in feedback_json and "should_redact" not in app_logs,
    }
    return {"ok": all(checks.values()), "checks": checks, "redis": redis_status.__dict__, "diagnostics": diagnostics}
