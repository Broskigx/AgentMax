"""Run skill pipelines step by step against an injected tool runner.

The runner is dependency-injected so the executor stays decoupled from
AgentMax's heavy ``ToolExecutor``/context machinery: pass any
``run_tool(tool_id, arguments) -> result`` callable whose result exposes
``success`` (bool) and ``output`` (dict). In production, wire it to
``ToolExecutor.execute`` / ``execute_step``; in tests, pass a fake.

Adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis),
licensed under the Apache License 2.0. See core/skills/NOTICE.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from core.skills.translator import ToolTranslator
from core.skills.types import SkillManifest, SkillResult, SkillStepResult

# A tool runner takes (tool_id, arguments) and returns a result object that
# exposes ``success`` (bool) and ``output`` (dict[str, Any]).
ToolRunner = Callable[[str, dict[str, Any]], Any]

_TEXT_KEYS = ("text", "stdout", "raw", "items", "result")


def render_arguments(template: str, context: dict[str, Any]) -> dict[str, Any]:
    """Fill ``{key}`` placeholders from ``context`` and parse the JSON result.

    Values are JSON-escaped before substitution so they remain valid inside the
    surrounding JSON string. Structural JSON braces (``{"..."``) are untouched
    because only exact ``{key}`` tokens for known context keys are replaced.
    """
    rendered = template
    for key, value in context.items():
        token = "{" + key + "}"
        if token in rendered:
            escaped = json.dumps(str(value))[1:-1]
            rendered = rendered.replace(token, escaped)
    return json.loads(rendered)


def _result_text(output: dict[str, Any]) -> str:
    for key in _TEXT_KEYS:
        if key in output and output[key]:
            value = output[key]
            return value if isinstance(value, str) else json.dumps(value, default=str)
    return json.dumps(output, default=str) if output else ""


class SkillExecutor:
    """Execute a :class:`SkillManifest` pipeline against a tool runner."""

    def __init__(self, run_tool: ToolRunner, *, translator: ToolTranslator | None = None) -> None:
        self._run_tool = run_tool
        self._translator = translator or ToolTranslator()

    def run(self, skill: SkillManifest, inputs: dict[str, Any] | None = None) -> SkillResult:
        """Run every step in order, threading outputs into the context."""
        context: dict[str, Any] = dict(inputs or {})
        results: list[SkillStepResult] = []

        for index, step in enumerate(skill.steps):
            if step.skill_name:
                return self._fail(
                    skill, results, f"step {index}: nested skills are not supported yet"
                )
            tool_id = self._translator.translate(step.tool_name)
            try:
                arguments = render_arguments(step.arguments_template, context)
            except (json.JSONDecodeError, ValueError) as exc:
                return self._fail(skill, results, f"step {index} ({tool_id}): bad template: {exc}")

            result = self._run_tool(tool_id, arguments)
            success = bool(getattr(result, "success", False))
            output = getattr(result, "output", {}) or {}
            text = _result_text(output)
            error = getattr(result, "error", None)
            results.append(
                SkillStepResult(
                    tool_id=tool_id,
                    output_key=step.output_key,
                    success=success,
                    text=text,
                    error=None if success else (error or "step failed"),
                )
            )
            if not success:
                return SkillResult(
                    skill=skill.name,
                    success=False,
                    outputs=context,
                    steps=results,
                    error=f"step {index} ({tool_id}) failed: {error or 'unknown error'}",
                )
            if step.output_key:
                context[step.output_key] = text

        return SkillResult(skill=skill.name, success=True, outputs=context, steps=results)

    @staticmethod
    def _fail(skill: SkillManifest, results: list[SkillStepResult], message: str) -> SkillResult:
        return SkillResult(skill=skill.name, success=False, steps=results, error=message)


__all__ = ["SkillExecutor", "ToolRunner", "render_arguments"]
