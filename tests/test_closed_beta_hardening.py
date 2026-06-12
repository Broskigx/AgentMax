from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agents.base_agent import ActionResult
from core.agents.supervisor import SupervisorAgent, TaskRecord, TaskRequest
from core.beta.diagnostics import _assert_safe_bundle
from core.feature_flags import FeatureFlags, load_config_profile
from core.htlgg import (
    DecisionRecord,
    ElementCandidate,
    HtlggBus,
    HtlggEnvelope,
    HtlggValidationError,
    RiskLevel,
    decode_record,
    encode_record,
    validate_record,
)
from core.htlgg.redis_bridge import HtlggRedisBridge
from core.input.human_simulator import HumanInputSimulator
from core.memory.visual_memory import VisualMemory
from core.runtime import AgentMaxRuntime
from core.security.permission_manager import PermissionManager
from core.security.policy import SecurityPolicy
from core.tools import ToolExecutionContext, ToolExecutor, ToolRequest, UserInputMonitor
from core.tools.registry import ToolRegistry
from core.tools.risk import ToolRiskAnalyzer


def _monitor() -> UserInputMonitor:
    return UserInputMonitor(
        cursor_provider=lambda: (0, 0),
        keyboard_provider=lambda: False,
        idle_after_sec=0.0,
        poll_interval_sec=0.005,
    )


def _permissions(*names: str, task_id: str = "task") -> PermissionManager:
    manager = PermissionManager(SimpleNamespace(require_consent=True))
    for name in names:
        manager.grant(name, task_id=task_id, ttl_sec=60)
    return manager


def test_config_precedence_and_closed_beta_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "agentmax.config.json").write_text(
        json.dumps(
            {
                "telemetry_enabled": True,
                "feature_flags": {
                    "mouse_control": True,
                    "keyboard_control": "false",
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "beta_config.json").write_text(
        json.dumps(
            {
                "telemetry_enabled": False,
                "feature_flags": {"mouse_control": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTMAX_FEATURE_KEYBOARD_CONTROL", "1")

    profile = load_config_profile(tmp_path)

    assert profile.data["telemetry_enabled"] is False
    assert profile.feature_flags.mouse_control is False
    assert profile.feature_flags.keyboard_control is True
    assert profile.feature_flags.terminal is False
    assert profile.feature_flags.file_actions is False
    assert profile.feature_flags.screen_vision is True
    assert profile.errors == []


@pytest.mark.asyncio
async def test_invalid_config_aborts_full_runtime() -> None:
    runtime = AgentMaxRuntime(config=SimpleNamespace(config_errors=["invalid JSON"]))
    with pytest.raises(ValueError, match="Invalid AgentMax configuration"):
        await runtime.start()


@pytest.mark.asyncio
async def test_supervisor_validation_rejects_failed_actions() -> None:
    supervisor = SupervisorAgent.__new__(SupervisorAgent)
    record = TaskRecord(request=TaskRequest(description="test validation"))
    record.results.append(
        ActionResult(
            success=False,
            error="expected visual change was absent",
            error_code="tool.verification_failed",
        )
    )

    with pytest.raises(RuntimeError, match="tool.verification_failed"):
        await SupervisorAgent._phase_validate(supervisor, record)


def test_scoped_permission_is_minimal_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(
        "core.security.permission_manager.time.monotonic",
        lambda: now[0],
    )
    manager = PermissionManager(SimpleNamespace(require_consent=True))
    manager.grant("SCREEN_READ", task_id="task-a", session_id="session-a", ttl_sec=2)

    assert manager.check("SCREEN_READ", task_id="task-a", session_id="session-a")
    assert not manager.check("SCREEN_READ", task_id="task-b", session_id="session-a")
    assert not manager.check("INPUT_MOUSE", task_id="task-a", session_id="session-a")

    now[0] = 102.1
    assert not manager.check("SCREEN_READ", task_id="task-a", session_id="session-a")


@pytest.mark.asyncio
async def test_feature_gate_prevents_handler_and_emits_htlgg_outcome() -> None:
    calls: list[tuple[int, int]] = []
    emitted = []

    class Sim:
        def move_to(self, x: int, y: int) -> None:
            calls.append((x, y))

    class Htlgg:
        async def emit(self, envelope: HtlggEnvelope) -> bool:
            emitted.append(envelope)
            return True

    executor = ToolExecutor(input_monitor=_monitor())
    result = await executor.execute(
        ToolRequest(
            tool_id="mouse.move",
            input={"x": 20, "y": 30},
            task_id="task",
        ),
        ToolExecutionContext(
            task_id="task",
            runtime=SimpleNamespace(htlgg=Htlgg()),
            agent_pool={"ui_automation": SimpleNamespace(_sim=Sim())},
            security=_permissions("INPUT_MOUSE", "SCREEN_READ"),
            extra={"feature_flags": FeatureFlags(mouse_control=False)},
        ),
    )

    assert result.success is False
    assert result.error_code == "tool.feature_disabled"
    assert calls == []
    assert emitted[0].record.outcome.value == "Y4"


@pytest.mark.asyncio
async def test_computer_execute_requests_only_permissions_it_uses() -> None:
    calls: list[list[dict]] = []

    class Capture:
        async def capture(self) -> dict:
            return {"frame": "metadata-only"}

    class Ui:
        async def execute_computer_actions(self, actions: list[dict]) -> ActionResult:
            calls.append(actions)
            return ActionResult(success=True, data={"results": [{"success": True}]})

    executor = ToolExecutor(input_monitor=_monitor())
    context = ToolExecutionContext(
        task_id="task",
        agent_pool={"ui_automation": Ui()},
        capture=Capture(),
        security=_permissions("INPUT_MOUSE", "SCREEN_READ"),
        extra={"feature_flags": FeatureFlags(mouse_control=True)},
    )
    mouse_only = await executor.execute(
        ToolRequest(
            tool_id="computer.execute",
            input={"actions": [{"action": "click", "x": 10, "y": 10}]},
            task_id="task",
        ),
        context,
    )
    assert mouse_only.success

    mixed = await executor.execute(
        ToolRequest(
            tool_id="computer.execute",
            input={
                "actions": [
                    {"action": "click", "x": 10, "y": 10},
                    {"action": "type", "text": "hello"},
                ]
            },
            task_id="task",
        ),
        context,
    )
    assert mixed.success is False
    assert mixed.error_code == "tool.feature_disabled"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_diagnostic_fallback_cannot_turn_failed_action_into_success() -> None:
    class Capture:
        async def capture(self) -> dict:
            return {"width": 100, "height": 100}

    class Ui:
        async def click(self, _step: dict) -> ActionResult:
            return ActionResult(success=False, error="click did not change the screen")

    executor = ToolExecutor(input_monitor=_monitor())
    result = await executor.execute(
        ToolRequest(
            tool_id="mouse.click",
            input={"x": 10, "y": 10},
            task_id="task",
        ),
        ToolExecutionContext(
            task_id="task",
            agent_pool={"ui_automation": Ui()},
            capture=Capture(),
            security=_permissions("INPUT_MOUSE", "SCREEN_READ"),
            extra={
                "feature_flags": FeatureFlags(
                    mouse_control=True,
                    screen_vision=True,
                )
            },
        ),
    )

    assert result.success is False
    assert result.error_code == "tool.action_failed"
    assert result.fallback_used == "screen.screenshot"
    assert result.output["fallback_observation"]["tool"] == "screen.screenshot"


@pytest.mark.asyncio
async def test_physical_actions_serialize_while_internal_work_concurs() -> None:
    class SlowExecutor(ToolExecutor):
        def __init__(self) -> None:
            super().__init__(input_monitor=_monitor())
            self.physical_active = 0
            self.max_physical_active = 0
            self.internal_overlapped = False
            self.started = asyncio.Event()

        async def _mouse_move(self, request, context):
            self.physical_active += 1
            self.max_physical_active = max(
                self.max_physical_active, self.physical_active
            )
            self.started.set()
            await asyncio.sleep(0.04)
            self.physical_active -= 1
            return self.normalizer.success(request, {"ok": True})

        async def _task_wait(self, request, context):
            self.internal_overlapped = self.physical_active > 0
            await asyncio.sleep(0.005)
            return self.normalizer.success(request, {"ok": True})

    executor = SlowExecutor()
    context = ToolExecutionContext(
        task_id="task",
        security=_permissions("INPUT_MOUSE", "SCREEN_READ"),
        extra={"feature_flags": FeatureFlags(mouse_control=True)},
    )
    first = asyncio.create_task(
        executor.execute(
            ToolRequest(
                tool_id="mouse.move",
                input={"x": 1, "y": 1},
                task_id="task",
            ),
            context,
        )
    )
    await executor.started.wait()
    second = asyncio.create_task(
        executor.execute(
            ToolRequest(
                tool_id="mouse.move",
                input={"x": 2, "y": 2},
                task_id="task",
            ),
            context,
        )
    )
    internal = asyncio.create_task(
        executor.execute(
            ToolRequest(
                tool_id="task.wait",
                input={"duration_sec": 0.1},
                task_id="task",
            ),
            context,
        )
    )
    results = await asyncio.gather(first, second, internal)

    assert all(result.success for result in results)
    assert executor.max_physical_active == 1
    assert executor.internal_overlapped is True


@pytest.mark.asyncio
async def test_animation_blocks_action_before_handler() -> None:
    calls: list[tuple[int, int]] = []

    class Capture:
        async def detect_animation(self) -> str:
            return "transition"

        async def wait_for_stable_screen(self) -> bool:
            return False

    class Sim:
        def move_to(self, x: int, y: int) -> None:
            calls.append((x, y))

    executor = ToolExecutor(input_monitor=_monitor())
    result = await executor.execute(
        ToolRequest(
            tool_id="mouse.move",
            input={"x": 20, "y": 30},
            task_id="task",
        ),
        ToolExecutionContext(
            task_id="task",
            capture=Capture(),
            agent_pool={"ui_automation": SimpleNamespace(_sim=Sim())},
            security=_permissions("INPUT_MOUSE", "SCREEN_READ"),
            extra={"feature_flags": FeatureFlags(mouse_control=True)},
        ),
    )

    assert result.success is False
    assert result.error_code == "tool.verification_failed"
    assert calls == []


def test_confidence_policy_thresholds_and_sensitive_surface() -> None:
    registry = ToolRegistry.default()
    risk = ToolRiskAnalyzer()
    click = registry.get("mouse.click")

    assert risk.verification_policy(click, {"x": 1, "y": 1}, confidence=0.95)[
        "action"
    ] == "normal"
    assert risk.verification_policy(click, {"x": 1, "y": 1}, confidence=0.75)[
        "action"
    ] == "pre_verify"
    assert risk.verification_policy(click, {"x": 1, "y": 1}, confidence=0.55)[
        "action"
    ] == "reobserve"
    assert risk.verification_policy(click, {"x": 1, "y": 1}, confidence=0.40)[
        "action"
    ] == "block"

    computer = registry.get("computer.execute")
    report = risk.analyze(
        computer,
        {"actions": [{"action": "type", "text": "submit payment"}]},
    )
    assert report["requires_confirmation"] is True


def test_drag_path_uses_press_interpolated_motion_and_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    simulator = object.__new__(HumanInputSimulator)
    simulator._last_x = 0
    simulator._last_y = 0
    events: list[tuple] = []
    simulator._clamp = lambda x, y: (x, y)
    simulator.move_to = lambda x, y: events.append(("move_to", x, y))
    simulator.mouse_down = lambda button="left": events.append(("down", button))
    simulator.mouse_up = lambda button="left": events.append(("up", button))
    simulator._raw_move = lambda x, y: events.append(("move", x, y))
    monkeypatch.setattr("core.input.human_simulator.time.sleep", lambda _value: None)

    simulator.drag_path([[0, 0], [64, 0]], duration_ms=10)

    assert events[0] == ("move_to", 0, 0)
    assert events[1] == ("down", "left")
    assert events[-1] == ("up", "left")
    moves = [event for event in events if event[0] == "move"]
    assert len(moves) >= 4
    assert moves[-1] == ("move", 64, 0)


@pytest.mark.asyncio
async def test_user_interruption_pause_block_and_authorized_resume() -> None:
    cursor = [0, 0]
    now = [0.0]
    authorized = [False]
    events = []

    class Bus:
        async def publish(self, event) -> None:
            events.append(event)

    monitor = UserInputMonitor(
        cursor_provider=lambda: (cursor[0], cursor[1]),
        keyboard_provider=lambda: False,
        clock=lambda: now[0],
        poll_interval_sec=0.005,
        idle_after_sec=2.0,
        bus=Bus(),
        authorization_validator=lambda _task_id: authorized[0],
    )
    monitor.start_task("task")
    cursor[:] = [50, 50]
    await asyncio.sleep(0.02)
    now[0] = 2.1
    await asyncio.sleep(0.02)
    authorized[0] = True
    await asyncio.sleep(0.02)
    monitor.stop_task()

    topics = [event.topic for event in events]
    assert "input.user_interrupted" in topics
    assert "input.agent_paused" in topics
    assert topics.count("input.agent_blocked") == 1
    assert "input.agent_resumed" in topics


def test_htlgg_codec_validation_and_redis_contract() -> None:
    candidate = ElementCandidate(
        id="save-button",
        text='Save "now"',
        bounds=(10, 20, 80, 30),
        confidence=87,
        source="ocr",
    )
    encoded = encode_record(candidate)
    assert encoded.startswith("E:")
    assert decode_record(encoded) == candidate

    with pytest.raises(HtlggValidationError):
        validate_record(
            DecisionRecord(
                action="filesystem.delete",
                risk=RiskLevel.R2,
                confirmed=False,
            )
        )

    class Redis:
        def __init__(self) -> None:
            self.calls = []

        def setex(self, *args) -> None:
            self.calls.append(("setex", args))

        def xadd(self, *args, **kwargs) -> None:
            self.calls.append(("xadd", args, kwargs))

        def expire(self, *args) -> None:
            self.calls.append(("expire", args))

    redis = Redis()
    bridge = HtlggRedisBridge(redis_client=redis)
    bridge.publish(
        "state",
        HtlggEnvelope(record=candidate, session_id="session"),
        encoded,
    )

    assert redis.calls[0][0] == "setex"
    assert redis.calls[0][1][1] == 300
    assert redis.calls[1][2]["maxlen"] == 1000
    assert redis.calls[2][1][1] == 900


@pytest.mark.asyncio
async def test_htlgg_bus_is_passive_bounded_and_redacts_images() -> None:
    bus = HtlggBus(max_items=2)
    invalid = await bus.emit(
        HtlggEnvelope(
            record=DecisionRecord(
                action="payment.submit",
                risk=RiskLevel.R3,
                confirmed=False,
            ),
            session_id="session",
        )
    )
    assert invalid is False

    for index in range(3):
        assert await bus.emit(
            HtlggEnvelope(
                record=ElementCandidate(
                    id=f"element-{index}",
                    text="visible",
                    bounds=(index, 0, 10, 10),
                    confidence=0.9,
                    source="vision",
                ),
                session_id="session",
                metadata={"screenshot": "base64-data", "token": "secret-value"},
            )
        )

    recent = bus.recent()
    assert len(recent) == 2
    assert recent[-1]["metadata"]["screenshot"] == "<SECRET>"
    assert recent[-1]["metadata"]["token"] == "<SECRET>"


def test_visual_memory_defaults_to_metadata_only_and_blocks_sensitive_screens(
    tmp_path: Path,
) -> None:
    memory = VisualMemory(
        SimpleNamespace(
            visual_cache_size=4,
            visual_cache_ttl_sec=30,
            visual_memory_path=str(tmp_path / "visual"),
            max_visual_templates=10,
            store_visual_images=False,
        )
    )
    try:
        template_id = memory.save_template(
            "editor",
            "save",
            {"bounds": [1, 2, 30, 40], "source": "vision"},
        )
        assert template_id
        entries = [
            json.loads(line)
            for line in (tmp_path / "visual" / "index.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert entries[0]["image_stored"] is False
        assert entries[0]["path"] is None
        assert list((tmp_path / "visual").glob("*.png")) == []

        blocked = memory.save_template(
            "browser login",
            "password field",
            {"bounds": [1, 2, 30, 40], "source": "vision"},
        )
        assert blocked == ""
    finally:
        memory.shutdown()


def test_shell_filesystem_and_diagnostics_policy(tmp_path: Path) -> None:
    policy = SecurityPolicy([str(tmp_path)])
    assert policy.validate_shell("echo ok", None).code == "shell.cwd_required"
    assert policy.validate_shell("git reset --hard", tmp_path).code == "shell.blocked"
    assert policy.validate_path(tmp_path / ".env", operation="read").allowed is False
    assert "SECRET" not in policy.scrub_environment({"SECRET": "x", "PATH": "ok"})
    assert "<EMAIL>" in policy.redact_output("user@example.com")

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "README.txt").write_text("safe", encoding="utf-8")
    _assert_safe_bundle(bundle)
    (bundle / ".env").write_text("TOKEN=secret", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected diagnostics artifact"):
        _assert_safe_bundle(bundle)


def test_dataset_settings_cannot_override_disabled_startup_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import agentpilot_test_server as server

    monkeypatch.setattr(server, "DATASET_ENABLED", False)
    monkeypatch.setattr(server, "DATASET_DIR", tmp_path)
    monkeypatch.setattr(server, "DATASET_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(server, "DATASET_FILE", tmp_path / "examples.jsonl")
    monkeypatch.setattr(server, "DATASET_IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(server.beta_consent, "is_enabled", lambda: True)
    server.DATASET_SETTINGS_FILE.write_text(
        json.dumps({"enabled": True, "store_images": True}),
        encoding="utf-8",
    )

    settings = server._dataset_settings()
    saved = server._save_dataset_settings(
        {"enabled": True, "store_images": True}
    )

    assert settings["enabled"] is False
    assert settings["store_images"] is False
    assert saved["enabled"] is False
    assert saved["store_images"] is False
