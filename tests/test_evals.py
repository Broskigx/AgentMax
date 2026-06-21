"""Tests for the eval harness: scorers, dataset loading, runner and report."""

from __future__ import annotations

from typing import Any

import pytest

from core.evals import (
    CallableScorer,
    Contains,
    DatasetError,
    EvalCase,
    ExactMatch,
    NormalizedMatch,
    NumericMatch,
    Regex,
    ScoreResult,
    load_cases,
    run_eval,
)


def _case(expected: str, **kw: Any) -> EvalCase:
    return EvalCase(input=kw.pop("input", "q"), expected=expected, **kw)


# ---------------------------------------------------------------------------
# scorers
# ---------------------------------------------------------------------------


def test_exact_match_normalizes_case_and_whitespace() -> None:
    s = ExactMatch()
    assert s.score(_case("Yes"), "  yes ").passed
    assert not s.score(_case("Yes"), "no").passed


def test_contains() -> None:
    assert Contains().score(_case("cat"), "the CAT sat").passed
    assert not Contains().score(_case("dog"), "the cat sat").passed


def test_regex() -> None:
    assert Regex(r"\d{3}-\d{4}").score(_case(""), "call 555-1234").passed
    assert not Regex(r"^\d+$").score(_case(""), "12a").passed


def test_numeric_match_with_tolerance_and_indeterminate() -> None:
    assert NumericMatch().score(_case("42"), "the answer is 42.").passed
    assert NumericMatch(tolerance=0.5).score(_case("10"), "10.4").passed
    indeterminate = NumericMatch().score(_case("10"), "no number here")
    assert indeterminate.passed is None


def test_normalized_match_numbers_lists_strings() -> None:
    s = NormalizedMatch()
    assert s.score(_case("1000"), "$1,000").passed  # number normalization
    assert s.score(_case("a, b, c"), "A,B,C").passed  # list normalization
    assert s.score(_case("Hello World"), "hello  world!").passed  # string normalization
    assert not s.score(_case("a, b"), "a, b, c").passed  # list length mismatch


def test_callable_scorer_accepts_bool_and_score_result() -> None:
    bool_scorer = CallableScorer(lambda case, ans: ans == case.expected, name="eq")
    assert bool_scorer.score(_case("x"), "x").passed

    rich = CallableScorer(
        lambda case, ans: ScoreResult(scorer="rich", passed=True, score=0.5),
        name="rich",
    )
    assert rich.score(_case("x"), "y").score == 0.5


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------


def test_load_cases(tmp_path: Any) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"id": "a", "input": "2+2", "expected": "4"}\n'
        "\n"  # blank line ignored
        '{"input": "cap of France", "expected": "Paris", "metadata": {"topic": "geo"}}\n',
        encoding="utf-8",
    )
    cases = load_cases(path)
    assert len(cases) == 2
    assert cases[0].id == "a"
    assert cases[1].id == "case-3"  # falls back to line number
    assert cases[1].metadata == {"topic": "geo"}


def test_load_cases_rejects_missing_input(tmp_path: Any) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"expected": "x"}\n', encoding="utf-8")
    with pytest.raises(DatasetError):
        load_cases(path)


def test_load_cases_rejects_bad_json(tmp_path: Any) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(DatasetError):
        load_cases(path)


# ---------------------------------------------------------------------------
# runner / report
# ---------------------------------------------------------------------------


def test_run_eval_aggregates_report() -> None:
    cases = [
        EvalCase(input="2+2", expected="4", id="a"),
        EvalCase(input="cap", expected="Paris", id="b"),
    ]
    answers = {"2+2": "4", "cap": "London"}
    report = run_eval(cases, lambda c: answers[c.input], ExactMatch())

    assert report.total == 2
    assert report.passed == 1
    assert report.pass_rate == 0.5
    assert report.summary()["pass_rate"] == 0.5


def test_run_eval_captures_target_errors() -> None:
    cases = [EvalCase(input="boom", expected="x", id="a")]

    def target(case: EvalCase) -> str:
        raise RuntimeError("kaboom")

    report = run_eval(cases, target, ExactMatch())
    assert report.errors == 1
    assert report.scored == []  # errored case is not scored
    assert report.results[0].error == "kaboom"
    assert report.results[0].score.passed is None


def test_run_eval_reraises_when_capture_disabled() -> None:
    cases = [EvalCase(input="boom", expected="x")]

    def target(case: EvalCase) -> str:
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        run_eval(cases, target, ExactMatch(), capture_errors=False)
