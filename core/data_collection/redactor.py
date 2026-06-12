"""Redaction utilities for AgentMax session logs."""

from __future__ import annotations

import json
import re
from typing import Any

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|clave)\b\s*[:=]\s*['\"]?([^\s,'\"]{6,})"
)
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]{16,}")
JWT_RE = re.compile(r"\b[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")
COOKIE_RE = re.compile(r"(?i)\b(cookie|set-cookie)\s*[:=]\s*([^\s;\n\r]{6,})")
AUTHORIZATION_RE = re.compile(r"(?i)\bauthorization\s*[:=]\s*([^\s;\n\r,]{6,})")
PRIVATE_IP_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|127\.0\.0\.1)\b"
)
WINDOWS_USER_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+")
POSIX_USER_PATH_RE = re.compile(r"(?i)(/home/[^/\s]+|/Users/[^/\s]+)")
MACHINE_RE = re.compile(r"(?i)\b(machine|computer|hostname)\s*[:=]\s*[A-Za-z0-9_.-]{2,}")


def redact_text(value: Any) -> str:
    text = str(value or "")
    text = EMAIL_RE.sub("<EMAIL>", text)
    text = SECRET_RE.sub(lambda m: f"{m.group(1)}=<SECRET>", text)
    text = AUTHORIZATION_RE.sub("Authorization=<SECRET>", text)
    text = COOKIE_RE.sub(lambda m: f"{m.group(1)}=<SECRET>", text)
    text = OPENAI_KEY_RE.sub("sk-<SECRET>", text)
    text = BEARER_RE.sub("Bearer <SECRET>", text)
    text = JWT_RE.sub("<JWT>", text)
    text = PRIVATE_IP_RE.sub("<PRIVATE_IP>", text)
    text = WINDOWS_USER_PATH_RE.sub("<USER_PATH>", text)
    text = POSIX_USER_PATH_RE.sub("<USER_PATH>", text)
    text = MACHINE_RE.sub(lambda m: f"{m.group(1)}=<MACHINE>", text)
    return text


def redact_record(record: Any) -> Any:
    if isinstance(record, str):
        return redact_text(record)
    if isinstance(record, list):
        return [redact_record(item) for item in record]
    if isinstance(record, dict):
        out: dict[str, Any] = {}
        for key, value in record.items():
            lowered = str(key).lower()
            if lowered in {
                "raw_response",
                "image_data",
                "data_url",
                "base64",
                "secret",
                "token",
                "api_key",
                "apikey",
                "authorization",
                "cookie",
                "set-cookie",
                "jwt",
                "password",
            }:
                out[key] = "<SECRET>"
            else:
                out[key] = redact_record(value)
        return out
    return record


def dumps_redacted(record: Any) -> str:
    return json.dumps(redact_record(record), ensure_ascii=False, separators=(",", ":"))


__all__ = ["dumps_redacted", "redact_record", "redact_text"]
