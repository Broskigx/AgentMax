"""Central shell and filesystem policy for closed-beta execution."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from core.data_collection.redactor import redact_text

DEFAULT_ALLOWED_DIRS = (
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "AppData" / "Local" / "AgentMax",
)

_BLOCKED_COMMANDS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\brm\s+-[^\r\n]*r[^\r\n]*f\b",
        r"\bdel\s+/[^\r\n]*s\b",
        r"\brd(?:ir)?\s+/[^\r\n]*s\b",
        r"\bformat(?:\.com)?\b",
        r"\bdiskpart\b",
        r"\breg\s+delete\b",
        r"\b(?:chmod|chown)\s+-[^\r\n]*r\b",
        r"\b(?:curl|wget)\b[^\r\n|]*\|\s*(?:bash|sh|powershell|cmd)\b",
        r"\b(?:irm|iwr|invoke-webrequest)\b[^\r\n|]*\|\s*(?:iex|invoke-expression)\b",
        r"\b(?:iex|invoke-expression)\s*\([^\r\n]*(?:downloadstring|webclient)",
        r"\bgit\s+(?:clean\s+-[^\r\n]*f|reset\s+--hard|push\s+--force)\b",
    )
)

_SENSITIVE_PATH = re.compile(
    r"(?i)(?:^|[\\/])("
    r"\.env(?:\.[^\\/]+)?|\.git|id_(?:rsa|dsa|ecdsa|ed25519)|"
    r"credentials?|cookies?|login data|web data|local state|"
    r"secrets?|tokens?|passwords?|private[_ -]?key|"
    r"\.aws|\.ssh|\.gnupg|browser profiles?"
    r")(?:$|[\\/])"
)

_COMPOUND_SHELL = re.compile(r"(?:&&|\|\||[|;`]|\r|\n)")

_EXFILTRATION = re.compile(
    r"(?i)\b(curl|wget|invoke-webrequest|iwr|scp|sftp|ftp)\b.*"
    r"(\.env|cookie|credential|token|password|private[_ -]?key|login data)"
)

_SAFE_ENV_KEYS = {
    "COMSPEC",
    "HOME",
    "LANG",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    code: str | None = None
    reason: str | None = None
    requires_confirmation: bool = False


def resolve_allowed_dirs(configured: Iterable[str] | None = None) -> list[Path]:
    values = [Path(value).expanduser() for value in (configured or []) if str(value).strip()]
    return [path.resolve() for path in (values or list(DEFAULT_ALLOWED_DIRS))]


def is_inside_allowed(path: Path, allowed_dirs: Iterable[Path]) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except Exception:
        return False
    return any(resolved == allowed or allowed in resolved.parents for allowed in allowed_dirs)


def is_sensitive_path(path: Path | str) -> bool:
    return bool(_SENSITIVE_PATH.search(str(path).replace("\\", "/")))


class SecurityPolicy:
    def __init__(self, allowed_dirs: Iterable[str] | None = None) -> None:
        self.allowed_dirs = resolve_allowed_dirs(allowed_dirs)

    def validate_shell(self, command: str, cwd: str | Path | None) -> PolicyDecision:
        if not command.strip():
            return PolicyDecision(False, "shell.empty", "Shell command is empty")
        if len(command) > 4096:
            return PolicyDecision(False, "shell.too_long", "Command exceeds 4096 characters")
        if cwd is None or not str(cwd).strip():
            return PolicyDecision(
                False,
                "shell.cwd_required",
                "Shell execution requires an explicit allowed working directory",
            )
        cwd_path = Path(cwd)
        if not is_inside_allowed(cwd_path, self.allowed_dirs):
            return PolicyDecision(
                False,
                "shell.cwd_outside_allowlist",
                f"Working directory is outside the allowed directories: {cwd_path}",
            )
        for pattern in _BLOCKED_COMMANDS:
            if pattern.search(command):
                return PolicyDecision(
                    False,
                    "shell.blocked",
                    f"Command matches blocked policy pattern: {pattern.pattern}",
                )
        if _COMPOUND_SHELL.search(command):
            return PolicyDecision(
                False,
                "shell.compound_blocked",
                "Compound commands, pipelines, and multi-line shell input are blocked",
            )
        if _EXFILTRATION.search(command):
            return PolicyDecision(
                False,
                "shell.exfiltration_blocked",
                "Command appears to transfer sensitive local data",
            )
        if _SENSITIVE_PATH.search(command.replace("\\", "/")):
            return PolicyDecision(
                False,
                "shell.sensitive_path",
                "Command references a protected secret or browser profile path",
            )
        return PolicyDecision(True, requires_confirmation=True)

    def validate_path(
        self,
        path: str | Path,
        *,
        operation: str,
        explicit_override: bool = False,
    ) -> PolicyDecision:
        candidate = Path(path)
        if is_sensitive_path(candidate):
            return PolicyDecision(
                False,
                "filesystem.sensitive_path",
                "Protected secret, credential, or browser profile path",
            )
        if not is_inside_allowed(candidate, self.allowed_dirs) and not explicit_override:
            return PolicyDecision(
                False,
                "filesystem.outside_allowlist",
                f"Path is outside allowed directories: {candidate}",
            )
        return PolicyDecision(
            True,
            requires_confirmation=operation in {"delete", "move", "write"},
        )

    @staticmethod
    def scrub_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
        env = source or os.environ
        return {key: value for key, value in env.items() if key.upper() in _SAFE_ENV_KEYS}

    @staticmethod
    def redact_output(value: str, *, limit: int = 16_384) -> str:
        return redact_text(value)[:limit]


__all__ = [
    "DEFAULT_ALLOWED_DIRS",
    "PolicyDecision",
    "SecurityPolicy",
    "is_inside_allowed",
    "is_sensitive_path",
    "resolve_allowed_dirs",
]
