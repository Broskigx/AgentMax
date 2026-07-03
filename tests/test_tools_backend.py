from __future__ import annotations

import json

import pytest

from core.cli import main as AGENTMAX_cli
from core.feature_flags import FeatureFlags
from core.tools import (
    SafeResumeManager,
    ToolExecutionContext,
    ToolExecutor,
    ToolRequest,
    ToolRiskAnalyzer,
    ToolRouter,
    ToolTestingRunner,
    UserInputMonitor,
)
from core.tools.registry import ToolRegistry
from core.tools.validator import ToolValidator


def test_tools_catalog_is_professional_and_valid() -> None:
    registry = ToolRegistry.default()
    report = registry.validate_catalog()
    snapshot = registry.snapshot()

    assert report.ok, report.error_text()
    assert snapshot["count"] >= 20
    for category in [
        "mouse",
        "keyboard",
        "screen",
        "window",
        "app",
        "filesystem",
        "shell",
        "browser",
        "memory",
        "reasoning",
        "task",
        "safety",
    ]:
        assert category in snapshot["categories"]

    mouse_move = registry.get("mouse.move")
    assert mouse_move.requires_user_idle is True
    assert "coordinates_inside_screen" in mouse_move.validation_rules
    assert mouse_move.retry_policy.max_attempts == 2


def test_tool_validator_rejects_bad_parameters_and_unsafe_context() -> None:
    registry = ToolRegistry.default()
    validator = ToolValidator()
    definition = registry.get("mouse.move")

    missing = validator.validate(
        definition,
        ToolRequest(tool_id="mouse.move", input={"x": 10}),
        ToolExecutionContext(screen_size=(100, 100), user_idle=True),
    )
    assert not missing.ok
    assert "Missing required field: y" in missing.error_text()

    out_of_bounds = validator.validate(
        definition,
        ToolRequest(tool_id="mouse.move", input={"x": 101, "y": 10}),
        ToolExecutionContext(screen_size=(100, 100), user_idle=True),
    )
    assert not out_of_bounds.ok
    assert "x=101 outside width 100" in out_of_bounds.error_text()

    busy_user = validator.validate(
        definition,
        ToolRequest(tool_id="mouse.move", input={"x": 10, "y": 10}),
        ToolExecutionContext(screen_size=(100, 100), user_idle=False),
    )
    assert not busy_user.ok
    assert "User input is active" in busy_user.error_text()


def test_tool_router_maps_legacy_steps_to_canonical_tools() -> None:
    router = ToolRouter()
    request = router.route_step(
        {"type": "click", "target": {"x": 12, "y": 30}, "click_type": "left"},
        task_id="task-1",
    )
    assert request.tool_id == "mouse.click"
    assert request.task_id == "task-1"
    assert request.input["x"] == 12
    assert request.input["y"] == 30

    shell = router.route_step({"type": "shell", "command": "echo hi", "timeout_sec": 2})
    assert shell.tool_id == "shell.run"
    assert shell.input["timeout_ms"] == 2000

    move = router.route_step({"type": "move_mouse", "x": 300, "y": 300})
    assert move.tool_id == "mouse.move"
    assert move.input == {"x": 300, "y": 300}


def test_risk_analyzer_blocks_destructive_shell_even_when_requested() -> None:
    registry = ToolRegistry.default()
    definition = registry.get("shell.run")
    analyzer = ToolRiskAnalyzer()
    payload = {"command": "rm -rf C:\\", "timeout_ms": 1000}

    risk = analyzer.analyze(definition, payload)
    assert risk["level"] == "critical"
    assert analyzer.blocks_execution(definition, payload) == "critical_risk_blocked"


@pytest.mark.asyncio
async def test_executor_dry_run_returns_normalized_result_shape() -> None:
    executor = ToolExecutor(ToolRegistry.default())
    result = await executor.execute(
        ToolRequest(tool_id="mouse.move", input={"x": 40, "y": 50}, dry_run=True),
        ToolExecutionContext(screen_size=(800, 600), user_idle=True),
    )
    payload = result.to_dict()

    assert result.success
    assert payload["tool"] == "mouse.move"
    assert payload["input"] == {"x": 40, "y": 50}
    assert payload["output"]["dry_run"] is True
    assert payload["error"] is None
    assert "duration_ms" in payload
    assert "confidence" in payload


@pytest.mark.asyncio
async def test_executor_blocks_high_risk_shell_without_confirmation() -> None:
    executor = ToolExecutor(ToolRegistry.default())
    result = await executor.execute(
        ToolRequest(tool_id="shell.run", input={"command": "echo hi", "timeout_ms": 1000}),
        ToolExecutionContext(
            user_idle=True,
            extra={"feature_flags": FeatureFlags(terminal=True)},
        ),
    )
    assert not result.success
    assert result.error_code == "tool.confirmation_required"


def test_user_input_monitor_detects_user_override_and_safe_resume() -> None:
    now = 0.0
    cursor = [0, 0]

    def clock() -> float:
        return now

    def cursor_provider() -> tuple[int, int]:
        return cursor[0], cursor[1]

    monitor = UserInputMonitor(cursor_provider=cursor_provider, clock=clock, idle_after_sec=2.0)

    cursor[:] = [25, 25]
    override, reason = monitor.override_detector.sample()
    assert override is True
    assert reason == "mouse_moved_by_user"

    monitor.record_agent_mouse_action(60, 60)
    cursor[:] = [60, 60]
    override, _ = monitor.override_detector.sample()
    assert override is False

    safe = SafeResumeManager(monitor.mouse, monitor.keyboard)
    can_resume, reason = safe.can_resume()
    assert can_resume is False
    assert reason == "mouse_not_idle"

    now = 2.1
    can_resume, reason = safe.can_resume()
    assert can_resume is True
    assert reason is None


@pytest.mark.asyncio
async def test_tool_testing_runner_doctor_and_dry_run_are_safe() -> None:
    runner = ToolTestingRunner()
    doctor = await runner.doctor()
    dry_run = await runner.run(safe=True, integration=False)

    assert doctor.ok, doctor.results
    assert dry_run.ok, dry_run.results
    assert dry_run.total >= 20
    assert all(item["dry_run"] for item in dry_run.results)


def test_AGENTMAX_tools_cli_validate_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = AGENTMAX_cli(["tools", "validate-json"])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert code == 0
    assert payload["ok"] is True
    assert payload["passed"] == 1


def test_AGENTMAX_doctor_alias(capsys: pytest.CaptureFixture[str]) -> None:
    code = AGENTMAX_cli(["doctor"])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert code == 0
    assert payload["ok"] is True
    assert any(item["name"] == "has_mouse_tools" for item in payload["results"])
