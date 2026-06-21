"""Lightweight evaluation harness for AgentMax.

Define eval cases, run them through any target (model/agent/skill), score the
answers with generic scorers, and aggregate a report. Self-contained: no model
backend or dataset download — plug an LLM judge via :class:`CallableScorer`.

Design adapted from OpenJarvis's evals layer (Apache-2.0). See core/evals/NOTICE.
"""

from core.evals.dataset import DatasetError, load_cases
from core.evals.runner import Target, run_eval
from core.evals.scorers import (
    CallableScorer,
    Contains,
    ExactMatch,
    NormalizedMatch,
    NumericMatch,
    Regex,
    Scorer,
)
from core.evals.types import CaseResult, EvalCase, EvalReport, ScoreResult

__all__ = [
    "load_cases",
    "DatasetError",
    "run_eval",
    "Target",
    "Scorer",
    "ExactMatch",
    "Contains",
    "Regex",
    "NumericMatch",
    "NormalizedMatch",
    "CallableScorer",
    "EvalCase",
    "ScoreResult",
    "CaseResult",
    "EvalReport",
]
