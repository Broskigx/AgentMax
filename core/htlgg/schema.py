from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class ExecutionOutcome(str, Enum):
    Y0 = "Y0"
    Y1 = "Y1"
    Y2 = "Y2"
    Y3 = "Y3"
    Y4 = "Y4"
    Y5 = "Y5"


class ErrorCode(str, Enum):
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"
    E5 = "E5"
    E6 = "E6"
    E7 = "E7"
    E8 = "E8"
    E9 = "E9"


class ControlToken(str, Enum):
    LOCK = "L*"
    BLOCK = "B*"
    CANCEL = "X*"
    ERROR = "E*"
    USER = "U*"
    SYNC = "S*"


@dataclass(frozen=True, slots=True)
class ElementCandidate:
    bounds: tuple[int, int, int, int]
    confidence: float
    source: str = "vision"
    text: str = ""
    id: str = ""
    element_type: str = "element"

    def __post_init__(self) -> None:
        value = float(self.confidence)
        if value > 1.0:
            value /= 100.0
        object.__setattr__(self, "confidence", max(0.0, min(1.0, value)))
        object.__setattr__(self, "bounds", tuple(int(v) for v in self.bounds))


@dataclass(frozen=True, slots=True)
class StateRecord:
    screen_id: str
    width: int
    height: int
    animation: str = "unknown"
    perceptual_hash: str = ""


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    action: str
    element_id: str = ""
    expected: str = ""
    risk: RiskLevel = RiskLevel.R0
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    action: str
    x: int | None = None
    y: int | None = None
    outcome: ExecutionOutcome = ExecutionOutcome.Y0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ControlRecord:
    token: ControlToken
    code: ErrorCode | None = None
    detail: str = ""


HtlggRecord = StateRecord | ElementCandidate | DecisionRecord | ExecutionRecord | ControlRecord


@dataclass(frozen=True, slots=True)
class HtlggEnvelope:
    record: HtlggRecord
    session_id: str
    task_id: str | None = None
    user_id: str = "local"
    version: str = "0.1"
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "ControlRecord",
    "ControlToken",
    "DecisionRecord",
    "ElementCandidate",
    "ErrorCode",
    "ExecutionOutcome",
    "ExecutionRecord",
    "HtlggEnvelope",
    "HtlggRecord",
    "RiskLevel",
    "StateRecord",
]
