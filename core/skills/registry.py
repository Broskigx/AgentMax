"""Runtime catalog of loaded skills."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from core.skills.loader import discover_skills
from core.skills.types import SkillManifest


class SkillRegistry:
    """In-memory catalog of skills, keyed by name."""

    def __init__(self, skills: Iterable[SkillManifest] | None = None) -> None:
        self._skills: dict[str, SkillManifest] = {}
        for skill in skills or []:
            self.register(skill)

    @classmethod
    def from_directory(cls, directory: str | Path) -> SkillRegistry:
        return cls(discover_skills(directory))

    @classmethod
    def default(cls) -> SkillRegistry:
        """Load the bundled skills from ``core/skills/data``."""
        return cls.from_directory(Path(__file__).resolve().parent / "data")

    def register(self, skill: SkillManifest) -> None:
        if not skill.name:
            raise ValueError("Skill name cannot be empty")
        if skill.name in self._skills:
            raise ValueError(f"Duplicate skill name: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillManifest:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {name}") from exc

    def maybe_get(self, name: str) -> SkillManifest | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def list(self, *, user_invocable_only: bool = False) -> list[SkillManifest]:
        skills = list(self._skills.values())
        if user_invocable_only:
            skills = [s for s in skills if s.user_invocable]
        return sorted(skills, key=lambda s: s.name)

    def __len__(self) -> int:
        return len(self._skills)


__all__ = ["SkillRegistry"]
