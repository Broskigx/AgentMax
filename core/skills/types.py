"""Skill type definitions.

Adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis),
licensed under the Apache License 2.0. See core/skills/NOTICE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SkillStep:
    """A single step in a skill pipeline."""

    tool_name: str = ""
    skill_name: str = ""  # invoke another skill instead of a tool
    arguments_template: str = "{}"  # JSON with {placeholders} filled from context
    output_key: str = ""  # key under which to store this step's result


@dataclass(slots=True)
class SkillManifest:
    """Manifest describing a reusable, multi-step skill."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    steps: list[SkillStep] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    user_invocable: bool = True
    disable_model_invocation: bool = False

    def manifest_bytes(self) -> bytes:
        """Serialize the manifest (stable order) for hashing/verification."""
        data = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "steps": [
                {
                    "tool_name": s.tool_name,
                    "skill_name": s.skill_name,
                    "arguments_template": s.arguments_template,
                    "output_key": s.output_key,
                }
                for s in self.steps
            ],
            "required_capabilities": self.required_capabilities,
            "tags": self.tags,
            "depends": self.depends,
        }
        return json.dumps(data, sort_keys=True).encode()


@dataclass(slots=True)
class SkillStepResult:
    """Outcome of a single executed step."""

    tool_id: str
    output_key: str
    success: bool
    text: str = ""
    error: str | None = None


@dataclass(slots=True)
class SkillResult:
    """Outcome of running a full skill pipeline."""

    skill: str
    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    steps: list[SkillStepResult] = field(default_factory=list)
    error: str | None = None


__all__ = ["SkillManifest", "SkillStep", "SkillStepResult", "SkillResult"]
