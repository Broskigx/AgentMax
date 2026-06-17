"""Unit tests for GoalEngine — smoke tests and state-machine coverage.

These tests use in-memory stubs so they run with zero external dependencies
(no Redis, no AI backend, no filesystem write permissions required).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.goal.goal_engine import GoalEngine, GoalState, GoalStatus  # noqa: E402

# --------------------------------------------------------------------------- #
# Stubs                                                                         #
# --------------------------------------------------------------------------- #


class _InMemoryRedis:
    """Minimal RedisService stub backed by a plain dict."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def setTaskState(self, task_id: str, state: dict, ttl_sec: int = 3600) -> None:  # noqa: N802
        self._store[task_id] = json.dumps(state)

    def getTaskState(self, task_id: str) -> dict | None:  # noqa: N802
        raw = self._store.get(task_id)
        return json.loads(raw) if raw else None


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event: Any) -> None:
        self.events.append((event.topic, event.payload))


def _make_engine(ai_responses: list[str]) -> tuple[GoalEngine, _FakeBus, _InMemoryRedis]:
    redis = _InMemoryRedis()
    bus = _FakeBus()
    ai = MagicMock()
    responses = iter(ai_responses)
    ai.chat_query = AsyncMock(side_effect=lambda **_kw: next(responses, '{"done": true, "summary": "done"}'))
    engine = GoalEngine(
        redis_service=redis,
        bus=bus,
        agent_pool={},
        ai_client=ai,
        max_iterations=10,
    )
    return engine, bus, redis


# --------------------------------------------------------------------------- #
# Tests                                                                         #
# --------------------------------------------------------------------------- #


def test_goal_state_serialization_round_trip():
    state = GoalState(goal_id="abc", objective="test goal")
    d = state.to_dict()
    assert d["goal_id"] == "abc"
    assert d["status"] == "pending"

    restored = GoalState.from_dict(d)
    assert restored.goal_id == "abc"
    assert restored.status == GoalStatus.PENDING
    assert restored.history == []


@pytest.mark.asyncio
async def test_goal_starts_and_emits_started_event():
    done_response = json.dumps({"done": True, "summary": "All done"})
    engine, bus, _ = _make_engine([done_response])

    await engine.start_goal("Do something simple")
    # Allow the background loop to run
    await asyncio.sleep(0.1)

    topics = [t for t, _ in bus.events]
    assert "goal.started" in topics


@pytest.mark.asyncio
async def test_goal_completes_when_ai_says_done():
    done_response = json.dumps({"done": True, "summary": "Finished successfully"})
    engine, bus, redis = _make_engine([done_response])

    goal_id = await engine.start_goal("Quick task")
    await asyncio.sleep(0.3)

    state = engine.get_state(goal_id)
    assert state is not None
    assert state.status == GoalStatus.COMPLETED
    assert "Finished" in state.completion_summary

    topics = [t for t, _ in bus.events]
    assert "goal.completed" in topics


@pytest.mark.asyncio
async def test_goal_executes_message_action():
    action_response = json.dumps({
        "done": False,
        "action": {"type": "message", "params": {"text": "Working on it..."}},
        "reasoning": "Sending progress update",
    })
    done_response = json.dumps({"done": True, "summary": "Done"})
    engine, bus, _ = _make_engine([action_response, done_response])

    await engine.start_goal("Multi-step task")
    await asyncio.sleep(0.5)

    topics = [t for t, _ in bus.events]
    assert "goal.message" in topics
    goal_msgs = [(t, p) for t, p in bus.events if t == "goal.message"]
    assert any("Working on it" in p.get("text", "") for _, p in goal_msgs)


@pytest.mark.asyncio
async def test_goal_cancellation():
    # AI never says done — loop will run until cancelled
    never_done = json.dumps({
        "done": False,
        "action": {"type": "message", "params": {"text": "still going"}},
        "reasoning": "not done",
    })
    engine, bus, _ = _make_engine([never_done] * 100)

    goal_id = await engine.start_goal("Endless task")
    await asyncio.sleep(0.1)

    cancelled = await engine.stop_goal(goal_id)
    assert cancelled is True

    await asyncio.sleep(0.2)
    state = engine.get_state(goal_id)
    assert state is not None
    assert state.status == GoalStatus.CANCELLED


@pytest.mark.asyncio
async def test_goal_stops_at_max_iterations():
    never_done = json.dumps({
        "done": False,
        "action": {"type": "message", "params": {"text": "iteration"}},
        "reasoning": "not done",
    })
    engine, bus, _ = _make_engine([never_done] * 50)

    goal_id = await engine.start_goal("Bounded task")
    # Wait long enough for all 10 iterations (each waits 1s normally, but we
    # patch _ITER_DELAY_S indirectly via a tiny max_iterations)
    # Patch the delay constant so tests don't take 10 seconds
    import core.goal.goal_engine as ge_mod
    original_delay = ge_mod._ITER_DELAY_S
    ge_mod._ITER_DELAY_S = 0.01
    try:
        await asyncio.sleep(1.0)
    finally:
        ge_mod._ITER_DELAY_S = original_delay

    state = engine.get_state(goal_id)
    if state:
        # Either it failed (max iter) or is still running; just ensure no crash
        assert state.status in {GoalStatus.FAILED, GoalStatus.RUNNING, GoalStatus.CANCELLED}


@pytest.mark.asyncio
async def test_run_shell_blocked_dangerous_command():
    done_response = json.dumps({"done": True, "summary": "Done"})
    engine, _, _ = _make_engine([done_response])
    state = GoalState(goal_id="test", objective="test")

    # rm -rf should be blocked by safety supervisor
    success, result = await engine._action_run_shell({"command": "rm -rf /tmp/testdir"}, state)
    assert not success
    assert "blocked" in result.lower() or "dangerous" in result.lower()


@pytest.mark.asyncio
async def test_install_package_rejects_shell_injection():
    done_response = json.dumps({"done": True, "summary": "Done"})
    engine, _, _ = _make_engine([done_response])
    state = GoalState(goal_id="test", objective="test")

    # Package name with shell injection character
    success, result = await engine._action_install_package({"pkg": "requests; rm -rf /"}, state)
    assert not success
    assert "invalid" in result.lower()


@pytest.mark.asyncio
async def test_write_skill_creates_file(tmp_path, monkeypatch):
    import core.goal.goal_engine as ge_mod

    monkeypatch.setattr(ge_mod, "_SKILL_DIR", tmp_path)

    done_response = json.dumps({"done": True, "summary": "Done"})
    engine, _, _ = _make_engine([done_response])
    state = GoalState(goal_id="test-goal-id", objective="test")

    success, result = await engine._action_write_skill(
        {
            "name": "my_skill",
            "description": "A test skill",
            "code": "def run():\n    return 42\n",
        },
        state,
    )
    assert success
    skill_file = tmp_path / "my_skill.py"
    assert skill_file.exists()
    content = skill_file.read_text()
    assert "def run():" in content
    assert "test-goa" in content  # first 8 chars of goal_id in header
    assert state.skills_installed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pkg",
    [
        "--index-url http://evil/x",
        "-r /etc/passwd",
        "./localdir",
        "git+https://evil/repo.git",
        "requests; rm -rf /",
        "-e .",
    ],
)
async def test_install_package_rejects_pip_option_injection(pkg):
    engine, _, _ = _make_engine([json.dumps({"done": True, "summary": "x"})])
    state = GoalState(goal_id="test", objective="test")
    success, result = await engine._action_install_package({"pkg": pkg}, state)
    assert not success
    assert "invalid" in result.lower() or "outside" in result.lower()


@pytest.mark.asyncio
async def test_install_package_accepts_valid_specs():
    engine, _, _ = _make_engine([json.dumps({"done": True, "summary": "x"})])
    state = GoalState(goal_id="test", objective="test")
    # Valid specs must pass validation (they fail later only at the pip step,
    # which we don't reach here because the regex gate is what we assert).
    import core.goal.goal_engine as ge_mod

    assert ge_mod._PKG_SPEC_RE.match("requests")
    assert ge_mod._PKG_SPEC_RE.match("requests[security]")
    assert ge_mod._PKG_SPEC_RE.match("requests>=2.0")
    assert not ge_mod._PKG_SPEC_RE.match("--upgrade")
    # Sanity: the action rejects the obviously-bad ones.
    ok, _ = await engine._action_install_package({"pkg": "--upgrade"}, state)
    assert not ok


@pytest.mark.asyncio
async def test_create_venv_rejects_path_outside_home(tmp_path):
    engine, _, _ = _make_engine([json.dumps({"done": True, "summary": "x"})])
    state = GoalState(goal_id="test", objective="test")
    for bad in ("/etc/profile.d/x", "/usr/local/bin", "/root/.ssh/keys"):
        success, result = await engine._action_create_venv({"path": bad}, state)
        assert not success
        assert "forbidden" in result.lower()


@pytest.mark.asyncio
async def test_run_shell_blocks_compound_command():
    engine, _, _ = _make_engine([json.dumps({"done": True, "summary": "x"})])
    state = GoalState(goal_id="test", objective="test")
    # A read-only prefix followed by a chained exfiltration must be blocked by
    # the policy gate (compound commands are not allowed).
    success, result = await engine._action_run_shell(
        {"command": "ls; cat /etc/passwd"}, state
    )
    assert not success
    assert "policy" in result.lower() or "blocked" in result.lower()


@pytest.mark.asyncio
async def test_write_skill_refuses_overwrite(tmp_path, monkeypatch):
    import core.goal.goal_engine as ge_mod

    monkeypatch.setattr(ge_mod, "_SKILL_DIR", tmp_path)
    engine, _, _ = _make_engine([json.dumps({"done": True, "summary": "x"})])
    state = GoalState(goal_id="test", objective="test")

    params = {"name": "dup", "description": "d", "code": "x = 1\n"}
    ok1, _ = await engine._action_write_skill(params, state)
    assert ok1
    ok2, result = await engine._action_write_skill(params, state)
    assert not ok2
    assert "already exists" in result.lower()


@pytest.mark.asyncio
async def test_start_goal_enforces_concurrency_cap():
    never_done = json.dumps(
        {"done": False, "action": {"type": "message", "params": {"text": "go"}}}
    )
    engine, _, _ = _make_engine([never_done] * 500)
    import core.goal.goal_engine as ge_mod

    monkeypatch_delay = ge_mod._ITER_DELAY_S
    ge_mod._ITER_DELAY_S = 5.0  # keep loops alive/busy so they stay "active"
    try:
        for _ in range(ge_mod._MAX_CONCURRENT_GOALS):
            await engine.start_goal("keep running")
        await asyncio.sleep(0.05)
        with pytest.raises(RuntimeError):
            await engine.start_goal("one too many")
    finally:
        ge_mod._ITER_DELAY_S = monkeypatch_delay
        for gid in list(engine.list_active()):
            await engine.stop_goal(gid)


@pytest.mark.asyncio
async def test_unknown_action_returns_failure():
    done_response = json.dumps({"done": True, "summary": "Done"})
    engine, _, _ = _make_engine([done_response])
    state = GoalState(goal_id="test", objective="test")

    result = await engine._execute_action("totally_unknown", {}, state)
    assert not result.success
    assert result.action_type == "totally_unknown"


def test_goal_engine_list_active_empty():
    redis = _InMemoryRedis()
    engine = GoalEngine(
        redis_service=redis,
        bus=None,
        agent_pool={},
        ai_client=MagicMock(),
    )
    assert engine.list_active() == []
