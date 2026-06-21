"""Generic, dependency-free scorers for the eval harness.

A scorer maps ``(case, answer)`` to a :class:`ScoreResult`. These cover the
common cases (exact / substring / regex / numeric / normalized match); plug a
custom or LLM-judge scorer via :class:`CallableScorer`.

The normalized-match logic is adapted from OpenJarvis's GAIA scorer
(Apache-2.0; see core/evals/NOTICE).
"""

from __future__ import annotations

import re
import string
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from core.evals.types import EvalCase, ScoreResult


@runtime_checkable
class Scorer(Protocol):
    """Scores a model answer against a case's reference."""

    name: str

    def score(self, case: EvalCase, answer: str) -> ScoreResult: ...


def _binary(name: str, passed: bool, **detail: object) -> ScoreResult:
    return ScoreResult(
        scorer=name, passed=passed, score=1.0 if passed else 0.0, detail=dict(detail)
    )


class ExactMatch:
    """Pass when the answer equals ``case.expected`` (optionally normalized)."""

    name = "exact_match"

    def __init__(self, *, case_sensitive: bool = False, strip: bool = True) -> None:
        self._case_sensitive = case_sensitive
        self._strip = strip

    def _prep(self, text: str) -> str:
        if self._strip:
            text = text.strip()
        return text if self._case_sensitive else text.lower()

    def score(self, case: EvalCase, answer: str) -> ScoreResult:
        return _binary(self.name, self._prep(answer) == self._prep(case.expected))


class Contains:
    """Pass when the answer contains ``case.expected`` as a substring."""

    name = "contains"

    def __init__(self, *, case_sensitive: bool = False) -> None:
        self._case_sensitive = case_sensitive

    def score(self, case: EvalCase, answer: str) -> ScoreResult:
        needle, hay = case.expected, answer
        if not self._case_sensitive:
            needle, hay = needle.lower(), hay.lower()
        return _binary(self.name, needle in hay)


class Regex:
    """Pass when the answer matches a regular expression.

    The pattern comes from ``pattern`` or, if omitted, ``case.expected``.
    """

    name = "regex"

    def __init__(self, pattern: str | None = None, *, flags: int = 0) -> None:
        self._pattern = pattern
        self._flags = flags

    def score(self, case: EvalCase, answer: str) -> ScoreResult:
        pattern = self._pattern if self._pattern is not None else case.expected
        matched = re.search(pattern, answer, self._flags) is not None
        return _binary(self.name, matched, pattern=pattern)


class NumericMatch:
    """Pass when the first number in the answer matches ``case.expected``."""

    name = "numeric_match"

    _NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

    def __init__(self, *, tolerance: float = 0.0) -> None:
        self._tolerance = tolerance

    def _first_number(self, text: str) -> float | None:
        cleaned = text.replace(",", "").replace("$", "").replace("%", "")
        match = self._NUMBER.search(cleaned)
        return float(match.group()) if match else None

    def score(self, case: EvalCase, answer: str) -> ScoreResult:
        got = self._first_number(answer)
        want = self._first_number(case.expected)
        if got is None or want is None:
            return ScoreResult(self.name, passed=None, detail={"got": got, "want": want})
        return _binary(self.name, abs(got - want) <= self._tolerance, got=got, want=want)


class NormalizedMatch:
    """Normalized exact match for numbers, comma/semicolon lists, and strings.

    Adapted from OpenJarvis's GAIA exact-match scorer.
    """

    name = "normalized_match"

    @staticmethod
    def _is_float(value: object) -> bool:
        try:
            float(value)  # type: ignore[arg-type]
            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _normalize_number(text: str) -> float:
        for char in ("$", "%", ","):
            text = text.replace(char, "")
        try:
            return float(text)
        except ValueError:
            return float("inf")

    @staticmethod
    def _normalize_str(text: str) -> str:
        no_spaces = re.sub(r"\s", "", text)
        translator = str.maketrans("", "", string.punctuation)
        return no_spaces.lower().translate(translator)

    @staticmethod
    def _split(text: str) -> list[str]:
        return re.split(r"[,;]", text)

    def _match(self, answer: str, expected: str) -> bool:
        if self._is_float(expected):
            return self._normalize_number(answer) == float(expected)
        if any(c in expected for c in (",", ";")):
            gt = self._split(expected)
            ma = self._split(answer)
            if len(gt) != len(ma):
                return False
            return all(self._match(a.strip(), b.strip()) for a, b in zip(ma, gt, strict=False))
        return self._normalize_str(answer) == self._normalize_str(expected)

    def score(self, case: EvalCase, answer: str) -> ScoreResult:
        return _binary(self.name, self._match(answer or "", case.expected))


class CallableScorer:
    """Wrap any ``(case, answer) -> bool | ScoreResult`` callable as a Scorer.

    Use this to plug a custom heuristic or an LLM-judge without coupling the
    harness to a model backend.
    """

    def __init__(self, fn: Callable[[EvalCase, str], bool | ScoreResult], *, name: str) -> None:
        self._fn = fn
        self.name = name

    def score(self, case: EvalCase, answer: str) -> ScoreResult:
        result = self._fn(case, answer)
        if isinstance(result, ScoreResult):
            return result
        return _binary(self.name, bool(result))


__all__ = [
    "Scorer",
    "ExactMatch",
    "Contains",
    "Regex",
    "NumericMatch",
    "NormalizedMatch",
    "CallableScorer",
]
