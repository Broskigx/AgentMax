from __future__ import annotations

import json
import re

from .schema import (
    ControlRecord,
    ControlToken,
    DecisionRecord,
    ElementCandidate,
    ErrorCode,
    ExecutionOutcome,
    ExecutionRecord,
    HtlggRecord,
    RiskLevel,
    StateRecord,
)
from .validator import validate_record

_ELEMENT_RE = re.compile(
    r'^E:([^#]+)#([^@]+)@(-?\d+),(-?\d+),(\d+),(\d+):("(?:\\.|[^"])*")'
    r"\|cf=(\d{1,3})\|src=([A-Za-z0-9_-]+)$"
)
_DECISION_RE = re.compile(
    r'^D:([^#]+)#([^=]*)=>("(?:\\.|[^"])*")\|(R[0-3])\|cfm=([01])$'
)
_EXEC_RE = re.compile(
    r'^X:([^@]+)@(-?\d*),(-?\d*)\|(Y[0-5])\|("(?:\\.|[^"])*")$'
)


def _quoted(value: str) -> str:
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("HTLGG text cannot contain control characters")
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def encode_record(record: HtlggRecord) -> str:
    validate_record(record)
    if isinstance(record, StateRecord):
        return (
            f"S:{record.screen_id}|{int(record.width)}x{int(record.height)}|"
            f"{record.animation}|{record.perceptual_hash}"
        )
    if isinstance(record, ElementCandidate):
        x, y, width, height = record.bounds
        return (
            f"E:{record.element_type}#{record.id}@{x},{y},{width},{height}:"
            f"{_quoted(record.text)}|cf={round(record.confidence * 100)}|src={record.source}"
        )
    if isinstance(record, DecisionRecord):
        return (
            f"D:{record.action}#{record.element_id}=>{_quoted(record.expected)}|"
            f"{record.risk.value}|cfm={int(record.confirmed)}"
        )
    if isinstance(record, ExecutionRecord):
        x = "" if record.x is None else str(int(record.x))
        y = "" if record.y is None else str(int(record.y))
        return (
            f"X:{record.action}@{x},{y}|{record.outcome.value}|"
            f"{_quoted(record.detail)}"
        )
    if isinstance(record, ControlRecord):
        if record.token is ControlToken.ERROR:
            if record.code is None:
                raise ValueError("E* control records require an E1-E9 code")
            return f"E*:{record.code.value}|{_quoted(record.detail)}"
        return f"{record.token.value}:{_quoted(record.detail)}"
    raise TypeError(f"Unsupported HTLGG record: {type(record).__name__}")


def decode_record(value: str) -> HtlggRecord:
    if "\n" in value or "\r" in value:
        raise ValueError("HTLGG records must be a single line")
    if value.startswith("S:"):
        screen_id, dimensions, animation, perceptual_hash = value[2:].split("|", 3)
        width, height = dimensions.split("x", 1)
        return StateRecord(screen_id, int(width), int(height), animation, perceptual_hash)
    if match := _ELEMENT_RE.fullmatch(value):
        return ElementCandidate(
            id=match.group(2),
            element_type=match.group(1),
            text=json.loads(match.group(7)),
            bounds=tuple(int(match.group(i)) for i in range(3, 7)),
            confidence=int(match.group(8)) / 100.0,
            source=match.group(9),
        )
    if match := _DECISION_RE.fullmatch(value):
        return DecisionRecord(
            action=match.group(1),
            element_id=match.group(2),
            expected=json.loads(match.group(3)),
            risk=RiskLevel(match.group(4)),
            confirmed=match.group(5) == "1",
        )
    if match := _EXEC_RE.fullmatch(value):
        return ExecutionRecord(
            action=match.group(1),
            x=int(match.group(2)) if match.group(2) else None,
            y=int(match.group(3)) if match.group(3) else None,
            outcome=ExecutionOutcome(match.group(4)),
            detail=json.loads(match.group(5)),
        )
    if value.startswith("E*:"):
        code, detail = value[3:].split("|", 1)
        return ControlRecord(ControlToken.ERROR, ErrorCode(code), json.loads(detail))
    for token in ControlToken:
        prefix = f"{token.value}:"
        if value.startswith(prefix):
            return ControlRecord(token, detail=json.loads(value[len(prefix) :]))
    raise ValueError("Invalid HTLGG v0.1 record")


__all__ = ["decode_record", "encode_record"]
