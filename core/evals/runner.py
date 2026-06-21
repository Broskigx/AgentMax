"""Run eval cases through a target callable and score the answers."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from core.evals.scorers import Scorer
from core.evals.types import CaseResult, EvalCase, EvalReport, ScoreResult

# The system under test: maps a case to the model/agent's answer text.
Target = Callable[[EvalCase], str]


def run_eval(
    cases: Iterable[EvalCase],
    target: Target,
    scorer: Scorer,
    *,
    capture_errors: bool = True,
) -> EvalReport:
    """Run every case through ``target``, score it, and aggregate a report.

    When ``capture_errors`` is true (default), an exception from ``target`` is
    recorded on that case (``error`` set, ``passed`` None) instead of aborting
    the whole run.
    """
    results: list[CaseResult] = []
    for index, case in enumerate(cases):
        case_id = case.id or f"case-{index}"
        try:
            answer = target(case)
        except Exception as exc:  # noqa: BLE001 - per-case isolation is the point
            if not capture_errors:
                raise
            results.append(
                CaseResult(
                    case_id=case_id,
                    answer="",
                    score=ScoreResult(scorer=scorer.name, passed=None),
                    error=str(exc),
                )
            )
            continue
        results.append(CaseResult(case_id=case_id, answer=answer, score=scorer.score(case, answer)))
    return EvalReport(results)


__all__ = ["run_eval", "Target"]
