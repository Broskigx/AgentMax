"""Central safety supervisor for local AgentMax tool calls.

This module is deliberately independent from the concrete executor so it can be
unit-tested and reused by CLI rescue mode, Python backend routes, and future
Tauri command bridges.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

RiskLevel = str


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    description: str
    risk_level: RiskLevel = "low"
    requires_approval: bool = False
    read_only: bool = True
    timeout_ms: int = 10_000
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class SupervisorDecision:
    allowed: bool
    requires_approval: bool = False
    blocked: bool = False
    reason: str = ""
    risk_level: RiskLevel = "low"
    safety_flags: list[str] = field(default_factory=list)


DANGEROUS_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bdel\s+/s\b", re.IGNORECASE),
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\btaskkill\b(?!.*(/pid|\b/im\b).*)", re.IGNORECASE),
    re.compile(r"\bstop-process\b", re.IGNORECASE),
    re.compile(r"\bremove-item\b.*(-recurse|-force)", re.IGNORECASE | re.DOTALL),
    re.compile(r"(iwr|irm|curl|wget).*(iex|invoke-expression|powershell|cmd)", re.IGNORECASE),
    re.compile(r"(curl|wget|iwr|irm)\b.*\|\s*(sh|bash|zsh|fish)\b", re.IGNORECASE),
    re.compile(r"\bset-executionpolicy\b.*\bbypass\b", re.IGNORECASE),
    re.compile(r"\b(sudo|runas)\b", re.IGNORECASE),
)

HIGH_RISK_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(npm|pnpm|yarn|pip|uv|cargo)\s+(install|add|remove|uninstall)\b", re.IGNORECASE),
    re.compile(r"\b(taskkill|stop-process|restart-service|stop-service)\b", re.IGNORECASE),
    re.compile(r"\b(reg\s+(add|delete)|set-itemproperty)\b", re.IGNORECASE),
    re.compile(r"\b(copy|move|ren|rename|set-content|out-file)\b", re.IGNORECASE),
)

READ_ONLY_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(dir|ls|whoami|tasklist|systeminfo)\b", re.IGNORECASE),
    re.compile(r"^\s*get-(process|childitem|wmiobject|ciminstance|winevent)\b", re.IGNORECASE),
    re.compile(r"^\s*wevtutil\s+qe\b", re.IGNORECASE),
)


DEFAULT_TOOL_POLICIES: dict[str, ToolPolicy] = {
    "screenshot": ToolPolicy(
        "screenshot", "Capture screen metadata/image with user consent.", "low", False, True, 8_000
    ),
    "run_cmd": ToolPolicy(
        "run_cmd", "Run cmd.exe commands under supervisor.", "medium", True, False, 30_000
    ),
    "run_powershell": ToolPolicy(
        "run_powershell", "Run PowerShell commands under supervisor.", "medium", True, False, 30_000
    ),
    "read_file": ToolPolicy(
        "read_file", "Read a file from an allowed workspace path.", "low", False, True, 10_000
    ),
    "write_file": ToolPolicy(
        "write_file", "Write a file after explicit approval.", "high", True, False, 10_000
    ),
    "list_dir": ToolPolicy("list_dir", "List directory contents.", "low", False, True, 8_000),
    "search_files": ToolPolicy(
        "search_files", "Search files by pattern.", "low", False, True, 15_000
    ),
    "open_app": ToolPolicy(
        "open_app",
        "Open an application visually (vision + mouse, no app-name detection).",
        "medium",
        True,
        False,
        10_000,
    ),
    "move_mouse": ToolPolicy(
        "move_mouse", "Move mouse without clicking.", "medium", True, False, 5_000
    ),
    "click": ToolPolicy(
        "click", "Click a verified UI coordinate or element.", "high", True, False, 5_000
    ),
    "type_text": ToolPolicy(
        "type_text", "Type text into the focused UI element.", "high", True, False, 10_000
    ),
    "wait": ToolPolicy("wait", "Wait for UI stability.", "low", False, True, 30_000),
    "stop_task": ToolPolicy(
        "stop_task", "Cancel the current agent task.", "low", False, True, 3_000
    ),
    "browser_web": ToolPolicy(
        "browser_web",
        "Use browser/web tools if connected.",
        "medium",
        True,
        False,
        20_000,
        "Browser connector is not always available.",
    ),
    "ocr": ToolPolicy(
        "ocr",
        "Extract visible text from screenshot if OCR is installed.",
        "low",
        False,
        True,
        15_000,
        "OCR is optional; use screenshot metadata fallback.",
    ),
}


class AgentToolSupervisor:
    """Evaluate tool calls before execution and detect repeated failures."""

    def __init__(
        self, policies: dict[str, ToolPolicy] | None = None, *, loop_limit: int = 3
    ) -> None:
        self.policies = policies or DEFAULT_TOOL_POLICIES
        self.loop_limit = max(2, int(loop_limit))
        self._failures: dict[tuple[str, str], list[float]] = {}
        self.cancelled = False

    def stop_task(self, reason: str = "User requested stop") -> SupervisorDecision:
        self.cancelled = True
        return SupervisorDecision(True, False, False, reason, "low", ["cancelled"])

    def reset(self) -> None:
        self.cancelled = False
        self._failures.clear()

    def inspect_command(self, command: str, *, approved: bool = False) -> SupervisorDecision:
        cmd = str(command or "").strip()
        if not cmd:
            return SupervisorDecision(
                False, False, True, "Command is empty.", "low", ["empty_command"]
            )

        for pattern in DANGEROUS_COMMAND_PATTERNS:
            if pattern.search(cmd):
                return SupervisorDecision(
                    False,
                    True,
                    True,
                    f"Blocked dangerous command pattern: {pattern.pattern}",
                    "critical",
                    ["dangerous_command"],
                )

        if any(pattern.search(cmd) for pattern in READ_ONLY_COMMAND_PATTERNS):
            return SupervisorDecision(
                True, False, False, "Read-only observation command allowed.", "low", ["read_only"]
            )

        if any(pattern.search(cmd) for pattern in HIGH_RISK_COMMAND_PATTERNS):
            return SupervisorDecision(
                bool(approved),
                not approved,
                False,
                "Command changes environment or process state and needs explicit approval.",
                "high",
                ["approval_required"],
            )

        return SupervisorDecision(
            bool(approved),
            not approved,
            False,
            "Command is not recognized as read-only; approval required before execution.",
            "medium",
            ["approval_required"],
        )

    def authorize_tool(
        self,
        tool_name: str,
        inputs: dict[str, Any] | None = None,
        *,
        approved: bool = False,
        read_only_authorized: bool = True,
    ) -> SupervisorDecision:
        if self.cancelled:
            return SupervisorDecision(
                False, False, True, "Task has been cancelled.", "low", ["cancelled"]
            )

        policy = self.policies.get(tool_name)
        if not policy:
            return SupervisorDecision(
                False, False, True, f"Unknown tool: {tool_name}", "critical", ["unknown_tool"]
            )

        if policy.unavailable_reason:
            return SupervisorDecision(
                False, False, True, policy.unavailable_reason, policy.risk_level, ["unavailable"]
            )

        if tool_name in {"run_cmd", "run_powershell"}:
            return self.inspect_command(str((inputs or {}).get("command", "")), approved=approved)

        if policy.read_only and read_only_authorized:
            return SupervisorDecision(
                True, False, False, "Read-only tool allowed.", policy.risk_level, ["read_only"]
            )

        if policy.requires_approval and not approved:
            return SupervisorDecision(
                False,
                True,
                False,
                f"{tool_name} requires explicit human approval.",
                policy.risk_level,
                ["approval_required"],
            )

        return SupervisorDecision(True, False, False, "Tool approved.", policy.risk_level, [])

    def record_failure(self, tool_name: str, error: str) -> SupervisorDecision:
        key = (tool_name, re.sub(r"\s+", " ", str(error or "unknown")).strip().lower()[:180])
        now = time.monotonic()
        entries = [ts for ts in self._failures.get(key, []) if now - ts < 300]
        entries.append(now)
        self._failures[key] = entries
        if len(entries) >= self.loop_limit:
            return SupervisorDecision(
                False,
                False,
                True,
                f"Loop detected: {tool_name} failed {len(entries)} times with the same error.",
                "high",
                ["loop_detected"],
            )
        return SupervisorDecision(
            True, False, False, "Failure recorded.", "medium", ["retry_allowed"]
        )


__all__ = [
    "AgentToolSupervisor",
    "DEFAULT_TOOL_POLICIES",
    "SupervisorDecision",
    "ToolPolicy",
]
