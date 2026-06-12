from __future__ import annotations

import re

from .schema import (
    DecisionRecord,
    ElementCandidate,
    ExecutionRecord,
    HtlggRecord,
    RiskLevel,
    StateRecord,
)

_SENSITIVE_ACTIONS = (
    "shell",
    "filesystem",
    "delete",
    "payment",
    "checkout",
    "login",
    "message",
    "submit",
    "credential",
)


class HtlggValidationError(ValueError):
    pass


_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_record(record: HtlggRecord) -> None:
    if isinstance(record, StateRecord):
        if not _TOKEN.fullmatch(record.screen_id):
            raise HtlggValidationError("screen id contains invalid characters")
        if "|" in record.animation or any(ch in record.animation for ch in "\r\n"):
            raise HtlggValidationError("animation contains invalid characters")
    if isinstance(record, ElementCandidate):
        if not record.id.strip():
            raise HtlggValidationError("element id is required")
        if not _TOKEN.fullmatch(record.id) or not _TOKEN.fullmatch(record.element_type):
            raise HtlggValidationError("element id/type contains invalid characters")
        if record.source not in {"ocr", "accessibility", "vision", "cache"}:
            raise HtlggValidationError(
                "element source must be ocr, accessibility, vision, or cache"
            )
        if len(record.bounds) != 4 or record.bounds[2] < 0 or record.bounds[3] < 0:
            raise HtlggValidationError("element bounds must be x,y,width,height")
        if not 0.0 <= record.confidence <= 1.0:
            raise HtlggValidationError("element confidence must be normalized to 0-1")
    if isinstance(record, DecisionRecord):
        if not _TOKEN.fullmatch(record.action):
            raise HtlggValidationError("decision action contains invalid characters")
        if record.element_id and not _TOKEN.fullmatch(record.element_id):
            raise HtlggValidationError("decision element id contains invalid characters")
        sensitive = any(term in record.action.lower() for term in _SENSITIVE_ACTIONS)
        if (record.risk in {RiskLevel.R2, RiskLevel.R3} or sensitive) and not record.confirmed:
            raise HtlggValidationError(
                "R2/R3 and sensitive decisions require confirmed=true"
            )
    if isinstance(record, ExecutionRecord) and not _TOKEN.fullmatch(record.action):
        raise HtlggValidationError("execution action contains invalid characters")


__all__ = ["HtlggValidationError", "validate_record"]
