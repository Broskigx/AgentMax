"""Core data types for the AgentMax eval harness.

Design adapted from OpenJarvis's evals layer (Apache-2.0; see core/evals/NOTICE)
but reduced to a self-contained core: no inference backend, dataset download, or
benchmark-specific coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EvalCase:
    """A single evaluation case: an input and its reference answer."""

    input: str
    expected: str = ""
    id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScoreResult:
    """The outcome of scoring one answer.

    ``passed`` is ``None`` when correctness could not be determined.
    ``score`` is a 0.0–1.0 number (1.0/0.0 for binary scorers).
    """

    scorer: str
    passed: bool | None
    score: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CaseResult:
    """The result of running and scoring a single case."""

    case_id: str
    answer: str
    score: ScoreResult
    error: str | None = None


@dataclass(slots=True)
class EvalReport:
    """Aggregate results for an eval run."""

    results: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @property
    def scored(self) -> list[CaseResult]:
        """Cases that produced a determinate pass/fail (no error, passed not None)."""
        return [r for r in self.results if r.error is None and r.score.passed is not None]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.scored if r.score.passed)

    @property
    def pass_rate(self) -> float:
        scored = self.scored
        return self.passed / len(scored) if scored else 0.0

    @property
    def mean_score(self) -> float:
        scored = self.scored
        return sum(r.score.score for r in scored) / len(scored) if scored else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "scored": len(self.scored),
            "passed": self.passed,
            "errors": self.errors,
            "pass_rate": round(self.pass_rate, 4),
            "mean_score": round(self.mean_score, 4),
        }


__all__ = ["EvalCase", "ScoreResult", "CaseResult", "EvalReport"]
