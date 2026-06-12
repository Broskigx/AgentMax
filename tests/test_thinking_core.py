import pytest

from core.ai.response_layer import ResponseLayer
from core.ai.thinking_engine import ThinkingEngine
from core.memory.short_term import ShortTermMemory


class _MemoryConfig:
    short_term_capacity = 20
    short_term_ttl_sec = 60


@pytest.mark.asyncio
async def test_thinking_core_creates_private_state_and_stores_summary() -> None:
    stm = ShortTermMemory(_MemoryConfig())
    engine = ThinkingEngine()

    state = await engine.prepare(
        task_id="task-1",
        user_input="Delete the temporary file at C:\\tmp\\old.txt",
        context={"stm": stm},
    )

    assert state.mode == "INTERNAL_THINKING_MODE"
    assert state.reasoning_depth.value in {"deep", "critical"}
    assert state.risk_score >= 0.5
    assert state.hidden_execution_plan == []
    assert state.checklist
    assert any(item.id == "risk_assessment" for item in state.checklist)
    assert state.planning_depth >= 1
    assert 0 <= state.uncertainty_score <= 1

    summary = stm.get("thinking:task-1")
    assert summary["state_id"] == state.state_id
    assert summary["checklist_score"] > 0
    assert summary["checklist"]
    assert "internal_notes" not in summary
    assert "hidden_execution_plan" not in summary


def test_tool_decision_rejects_dangerous_unconfirmed_step() -> None:
    engine = ThinkingEngine()
    step = {"type": "delete_file", "path": "C:\\tmp\\old.txt", "critical": True}

    decision = engine.decide_step(None, step, confirmed=False)

    assert decision.allowed is False
    assert "confirmation" in decision.reason.lower()
    assert decision.normalized_step["risk_score"] >= 0.8
    assert "confirmation_gate" in decision.validation_pipeline
    assert decision.permission_level == "destructive"
    assert decision.sandbox_mode == "workspace"


def test_tool_decision_hard_blocks_broad_delete() -> None:
    engine = ThinkingEngine()
    step = {"type": "delete_file", "path": "C:\\", "critical": True}

    decision = engine.decide_step(None, step, confirmed=True)

    assert decision.allowed is False
    assert "root-level delete" in decision.reason


def test_shell_compound_command_requires_decomposition() -> None:
    engine = ThinkingEngine()
    step = {"type": "shell", "command": "Get-Process | Stop-Process -Force"}

    decision = engine.decide_step(None, step, confirmed=True)

    assert decision.allowed is False
    assert "decomposition" in decision.reason


def test_tool_registry_exposes_execution_policy_manifest() -> None:
    engine = ThinkingEngine()

    manifest = engine.tool_manifest()
    shell = next(item for item in manifest if item["step_type"] == "shell")

    assert shell["execution_mode"] == "sandboxed"
    assert shell["retry_policy"]["max_attempts"] >= 1
    assert shell["confidence_threshold"] >= 0.7
    assert "sandbox_scope" in shell["validation_pipeline"]


@pytest.mark.asyncio
async def test_attach_plan_updates_checklist_and_tool_metadata() -> None:
    engine = ThinkingEngine()
    step = {"type": "click", "description": "Open settings", "target": {"text": "Settings"}}
    state = await engine.prepare(
        task_id="task-2",
        user_input="Click the Settings button",
        context={},
    )

    enriched = engine.attach_plan(state, [step])

    assert enriched[0]["internal_tool_priority"] > 0
    assert enriched[0]["validation_pipeline"]
    checklist = {item.id: item.status.value for item in state.checklist}
    assert checklist["tool_strategy"] == "passed"
    assert checklist["postcondition_validation"] == "passed"


@pytest.mark.asyncio
async def test_failed_step_creates_recovery_reflection() -> None:
    engine = ThinkingEngine()
    state = await engine.prepare(
        task_id="task-3",
        user_input="Click the missing Save button",
        context={},
    )
    step = {"type": "click", "description": "Click Save", "target": {"text": "Save"}}

    engine.observe_step_result(state, step=step, success=False, error="target not found")
    recovery = engine.recovery_steps(state, step, error="target not found")

    assert state.tool_reflections
    assert state.uncertainty_score > 0
    assert any(
        strategy in state.recovery_strategies
        for strategy in ["fallback_screenshot", "replan_target"]
    )
    assert recovery


def test_response_layer_strips_private_fields() -> None:
    raw = (
        '{"think":"private","reply":"Visible response",'
        '"action":"none","hidden_execution_plan":[{"type":"shell"}]}'
    )

    visible = ResponseLayer().visible_chat_payload(raw)

    assert visible == {"reply": "Visible response", "action": "none", "task": None}


def test_response_layer_strips_nested_private_fields_and_invalid_action() -> None:
    raw = (
        '{"reply":"Visible","action":"debug","task":123,'
        '"meta":{"chain_of_thought":"secret","safe":"ok"}}'
    )

    visible = ResponseLayer().visible_chat_payload(raw)

    assert visible == {"reply": "Visible", "action": "none", "task": "123"}
