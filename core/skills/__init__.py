"""Skill system for AgentMax — reusable, declarative multi-tool pipelines.

A skill (``core/skills/data/*.toml``) is a named sequence of steps; each step
invokes a tool with a templated argument string and stores its result for later
steps. Loading/validation is decoupled from execution: :class:`SkillRegistry`
catalogs manifests, :class:`SkillExecutor` runs them against an injected tool
runner, and :class:`ToolTranslator` maps OpenJarvis tool names to AgentMax ids.

Portions adapted from OpenJarvis (Apache-2.0). See core/skills/NOTICE.
"""

from core.skills.executor import SkillExecutor, ToolRunner, render_arguments
from core.skills.loader import (
    SkillLoadError,
    discover_skills,
    load_skill,
    manifest_from_toml,
)
from core.skills.manager import SkillManager
from core.skills.registry import SkillRegistry
from core.skills.translator import TOOL_TRANSLATION, ToolTranslator
from core.skills.types import SkillManifest, SkillResult, SkillStep, SkillStepResult

__all__ = [
    "SkillExecutor",
    "ToolRunner",
    "render_arguments",
    "SkillLoadError",
    "discover_skills",
    "load_skill",
    "manifest_from_toml",
    "SkillManager",
    "SkillRegistry",
    "TOOL_TRANSLATION",
    "ToolTranslator",
    "SkillManifest",
    "SkillStep",
    "SkillStepResult",
    "SkillResult",
]
