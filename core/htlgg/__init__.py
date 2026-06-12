"""HTLGG v0.1 passive state/decision/execution protocol."""

from .bus import HtlggBus
from .codec import decode_record, encode_record
from .schema import (
    ControlRecord,
    ControlToken,
    DecisionRecord,
    ElementCandidate,
    ErrorCode,
    ExecutionRecord,
    ExecutionOutcome,
    HtlggEnvelope,
    RiskLevel,
    StateRecord,
)
from .validator import HtlggValidationError, validate_record

__all__ = [
    "ControlRecord",
    "ControlToken",
    "DecisionRecord",
    "ElementCandidate",
    "ErrorCode",
    "ExecutionOutcome",
    "ExecutionRecord",
    "HtlggBus",
    "HtlggEnvelope",
    "HtlggValidationError",
    "RiskLevel",
    "StateRecord",
    "decode_record",
    "encode_record",
    "validate_record",
]
