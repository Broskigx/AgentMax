"""Tests for the skills layer: loader, registry, translator and executor.

Covers:
  - loading the bundled .toml skills into a registry
  - tool-name translation (OpenJarvis -> AgentMax)
  - argument-template rendering with JSON escaping
  - pipeline execution: ordering, output threading, failure short-circuit
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.skills import (
    SkillExecutor,
    SkillManager,
    SkillManifest,
    SkillRegistry,
    SkillStep,
    ToolTranslator,
    load_skill,
    render_arguments,
)
from core.skills.loader import SkillLoadError, manifest_from_toml
from core.tools.executor import ToolExecutor
from core.tools.models import ToolExecutionContext, ToolRequest
from core.tools.registry import ToolRegistry as ToolCatalog


@dataclass
class _FakeResult:
    success: bool = True
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class _RecordingRunner:
    """Captures (tool_id, arguments) calls and returns canned outputs."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fail_on = fail_on

    def __call__(self, tool_id: str, arguments: dict[str, Any]) -> _FakeResult:
        self.calls.append((tool_id, arguments))
        if tool_id == self._fail_on:
            return _FakeResult(success=False, error="boom")
        return _FakeResult(success=True, output={"text": f"ran {tool_id}"})


# ---------------------------------------------------------------------------
# loader / registry
# ---------------------------------------------------------------------------


def test_default_registry_loads_bundled_skills() -> None:
    registry = SkillRegistry.default()
    assert len(registry) == 20
    assert "file-organizer" in registry.names()
    for skill in registry.list():
        assert skill.name
        assert skill.steps  # every bundled skill has at least one step


def test_file_organizer_parses_steps() -> None:
    skill = SkillRegistry.default().get("file-organizer")
    assert skill.steps[0].tool_name == "shell_exec"
    assert skill.steps[0].output_key == "file_listing"
    assert any(s.tool_name == "think" for s in skill.steps)


def test_manifest_from_toml_requires_skill_table() -> None:
    try:
        manifest_from_toml({"not_skill": {}})
    except SkillLoadError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected SkillLoadError")


def test_load_skill_roundtrip(tmp_path: Any) -> None:
    path = tmp_path / "demo.toml"
    path.write_text(
        '[skill]\nname = "demo"\ndescription = "d"\n'
        '[[skill.steps]]\ntool_name = "think"\narguments_template = "{}"\noutput_key = "x"\n',
        encoding="utf-8",
    )
    skill = load_skill(path)
    assert skill.name == "demo"
    assert skill.steps[0].tool_name == "think"


# ---------------------------------------------------------------------------
# translator
# ---------------------------------------------------------------------------


def test_translator_maps_known_and_passes_unknown() -> None:
    t = ToolTranslator()
    assert t.translate("think") == "reasoning.raw"
    assert t.translate("shell_exec") == "shell.run"
    assert t.translate("file_read") == "filesystem.read"
    assert t.translate("totally_unknown") == "totally_unknown"
    assert not t.is_known("totally_unknown")


# ---------------------------------------------------------------------------
# template rendering
# ---------------------------------------------------------------------------


def test_render_arguments_substitutes_and_preserves_json() -> None:
    out = render_arguments('{"command": "find {directory} -type f"}', {"directory": "/tmp/data"})
    assert out == {"command": "find /tmp/data -type f"}


def test_render_arguments_escapes_values() -> None:
    out = render_arguments('{"q": "{text}"}', {"text": 'say "hi"\nbye'})
    assert out == {"q": 'say "hi"\nbye'}


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------


def _two_step_skill() -> SkillManifest:
    return SkillManifest(
        name="demo",
        steps=[
            SkillStep(
                tool_name="shell_exec",
                arguments_template='{"command": "ls {directory}"}',
                output_key="listing",
            ),
            SkillStep(
                tool_name="think",
                arguments_template='{"thought": "summarize: {listing}"}',
                output_key="summary",
            ),
        ],
    )


def test_executor_runs_pipeline_with_translation_and_threading() -> None:
    runner = _RecordingRunner()
    result = SkillExecutor(runner).run(_two_step_skill(), {"directory": "/tmp"})

    assert result.success
    # Tool names were translated to AgentMax ids.
    assert [c[0] for c in runner.calls] == ["shell.run", "reasoning.raw"]
    # First step args were rendered from the input...
    assert runner.calls[0][1] == {"command": "ls /tmp"}
    # ...and the first step's output was threaded into the second step.
    assert runner.calls[1][1] == {"thought": "summarize: ran shell.run"}
    assert result.outputs["summary"] == "ran reasoning.raw"


def test_executor_short_circuits_on_failure() -> None:
    runner = _RecordingRunner(fail_on="shell.run")
    result = SkillExecutor(runner).run(_two_step_skill(), {"directory": "/tmp"})

    assert not result.success
    assert "step 0" in result.error
    assert len(runner.calls) == 1  # second step never ran


def test_executor_reports_bad_template() -> None:
    skill = SkillManifest(
        name="bad",
        steps=[SkillStep(tool_name="think", arguments_template='{"x": {missing}}')],
    )
    result = SkillExecutor(_RecordingRunner()).run(skill, {})
    assert not result.success
    assert "bad template" in result.error


# ---------------------------------------------------------------------------
# manager <-> tool registry bridge
# ---------------------------------------------------------------------------


def _echo_skill() -> SkillManifest:
    return SkillManifest(
        name="echo",
        description="Echo via reasoning.",
        steps=[
            SkillStep(
                tool_name="think",
                arguments_template='{"value": "{message}"}',
                output_key="out",
            )
        ],
    )


def test_manager_registers_skills_as_tools() -> None:
    manager = SkillManager(SkillRegistry([_echo_skill()]))
    catalog = ToolCatalog()
    added = manager.register_into(catalog)

    assert added == ["skill.echo"]
    definition = catalog.get("skill.echo")
    assert definition.category == "skill"
    assert manager.register_into(catalog) == []  # idempotent


def test_manager_run_skill_with_injected_runner() -> None:
    manager = SkillManager(SkillRegistry([_echo_skill()]))
    manager.register_into(ToolCatalog())
    runner = _RecordingRunner()
    result = manager.run_skill("skill.echo", {"message": "hi"}, runner)

    assert result.success
    assert runner.calls[0][0] == "reasoning.raw"  # translated


# ---------------------------------------------------------------------------
# executor routing (full pipeline, real tool steps)
# ---------------------------------------------------------------------------


class _GrantAll:
    """Minimal security manager that grants every permission."""

    def check(self, permission: str, **kwargs: Any) -> bool:
        return True


def _resolution_skill() -> SkillManifest:
    # Uses screen.resolution: low-risk, runs without an agent pool, and its
    # 'read' permission resolves to SCREEN_READ for the 'screen' category.
    return SkillManifest(
        name="res",
        description="Read screen resolution.",
        steps=[SkillStep(tool_name="screen.resolution", arguments_template="{}", output_key="res")],
    )


async def test_executor_routes_skill_through_full_pipeline() -> None:
    manager = SkillManager(SkillRegistry([_resolution_skill()]))
    catalog = ToolCatalog.default()  # provides screen.resolution for the inner step
    manager.register_into(catalog)

    executor = ToolExecutor(catalog, skill_manager=manager)
    result = await executor._run_builtin_tool(
        catalog.get("skill.res"),
        ToolRequest(tool_id="skill.res", input={}),
        ToolExecutionContext(security=_GrantAll(), screen_size=(800, 600)),
    )
    assert result.success
    assert result.output["steps"] == ["screen.resolution"]
    assert "res" in result.output["outputs"]


async def test_executor_without_skill_manager_reports_handler_missing() -> None:
    manager = SkillManager(SkillRegistry([_echo_skill()]))
    catalog = ToolCatalog.default()
    manager.register_into(catalog)

    executor = ToolExecutor(catalog)  # no skill_manager
    result = await executor._run_builtin_tool(
        catalog.get("skill.echo"),
        ToolRequest(tool_id="skill.echo", input={"message": "hi"}),
        ToolExecutionContext(),
    )
    assert not result.success
    assert result.error_code == "tool.handler_missing"
