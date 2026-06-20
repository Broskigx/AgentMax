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
    SkillManifest,
    SkillRegistry,
    SkillStep,
    ToolTranslator,
    load_skill,
    render_arguments,
)
from core.skills.loader import SkillLoadError, manifest_from_toml


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
