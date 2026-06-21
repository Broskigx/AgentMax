"""Tests for wiring capability managers (skills/MCP/A2A) into the tool layer."""

from __future__ import annotations

from core.skills import SkillManifest, SkillRegistry, SkillStep
from core.tools.executor import ToolExecutor
from core.tools.integrations import CapabilityManagers, wire_capabilities
from core.tools.models import ToolExecutionContext, ToolRequest
from core.tools.registry import ToolRegistry


def test_wire_capabilities_registers_bundled_skills() -> None:
    registry = ToolRegistry.default()
    caps = wire_capabilities(registry)

    assert isinstance(caps, CapabilityManagers)
    assert len(caps.registered_skill_ids) == 20
    assert registry.maybe_get("skill.file-organizer") is not None
    # MCP/A2A managers are present and empty (connect lazily at runtime).
    assert caps.mcp.servers() == []
    assert caps.a2a.agents() == []


def test_wire_capabilities_skill_registration_is_idempotent() -> None:
    registry = ToolRegistry.default()
    wire_capabilities(registry)
    second = wire_capabilities(registry)
    assert second.registered_skill_ids == []


def test_wire_capabilities_can_skip_skill_registration() -> None:
    registry = ToolRegistry.default()
    caps = wire_capabilities(registry, register_skills=False)
    assert caps.registered_skill_ids == []
    assert registry.maybe_get("skill.file-organizer") is None


async def test_wired_executor_runs_a_skill_end_to_end() -> None:
    # A think-only skill: its single step maps to reasoning.raw, which runs
    # through the full pipeline without a security manager.
    skill = SkillManifest(
        name="ping",
        steps=[
            SkillStep(
                tool_name="think",
                arguments_template='{"value": "{msg}"}',
                output_key="out",
            )
        ],
    )
    registry = ToolRegistry.default()
    caps = wire_capabilities(registry, skill_registry=SkillRegistry([skill]))
    executor = ToolExecutor(
        registry,
        skill_manager=caps.skills,
        mcp_manager=caps.mcp,
        a2a_manager=caps.a2a,
    )

    result = await executor._run_builtin_tool(
        registry.get("skill.ping"),
        ToolRequest(tool_id="skill.ping", input={"msg": "hi"}),
        ToolExecutionContext(),
    )
    assert result.success
    assert result.output["steps"] == ["reasoning.raw"]
