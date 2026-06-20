"""Load step-pipeline skills from ``.toml`` files into SkillManifest objects."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from core.skills.types import SkillManifest, SkillStep


class SkillLoadError(ValueError):
    """Raised when a skill file cannot be parsed into a manifest."""


def _step_from_dict(raw: dict[str, Any]) -> SkillStep:
    return SkillStep(
        tool_name=str(raw.get("tool_name", "")),
        skill_name=str(raw.get("skill_name", "")),
        arguments_template=str(raw.get("arguments_template", "{}")),
        output_key=str(raw.get("output_key", "")),
    )


def manifest_from_toml(data: dict[str, Any]) -> SkillManifest:
    """Build a :class:`SkillManifest` from parsed TOML data."""
    skill = data.get("skill")
    if not isinstance(skill, dict):
        raise SkillLoadError("Missing [skill] table")
    name = skill.get("name")
    if not name:
        raise SkillLoadError("Skill is missing a name")
    steps_raw = skill.get("steps", [])
    if not isinstance(steps_raw, list):
        raise SkillLoadError(f"Skill '{name}' has a non-list steps field")
    return SkillManifest(
        name=str(name),
        version=str(skill.get("version", "0.1.0")),
        description=str(skill.get("description", "")),
        author=str(skill.get("author", "")),
        steps=[_step_from_dict(s) for s in steps_raw if isinstance(s, dict)],
        required_capabilities=[str(c) for c in skill.get("required_capabilities", [])],
        tags=[str(t) for t in skill.get("tags", [])],
        depends=[str(d) for d in skill.get("depends", [])],
        user_invocable=bool(skill.get("user_invocable", True)),
        disable_model_invocation=bool(skill.get("disable_model_invocation", False)),
    )


def load_skill(path: str | Path) -> SkillManifest:
    """Load a single ``.toml`` skill file."""
    file = Path(path)
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SkillLoadError(f"Cannot read skill {file}: {exc}") from exc
    return manifest_from_toml(data)


def discover_skills(directory: str | Path) -> list[SkillManifest]:
    """Load every ``*.toml`` skill in ``directory`` (sorted by filename)."""
    return [load_skill(p) for p in sorted(Path(directory).glob("*.toml"))]


__all__ = ["SkillLoadError", "discover_skills", "load_skill", "manifest_from_toml"]
