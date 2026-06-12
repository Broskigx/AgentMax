"""Security Agent -- enforces permission policy, rate limits, and consent requirements."""

from __future__ import annotations

import re
import time
from collections import deque
from typing import TYPE_CHECKING

import structlog

from core.agents.base_agent import AgentCapability, AgentContext, BaseAgent
from core.security.policy import SecurityPolicy

if TYPE_CHECKING:
    from core.agents.supervisor import TaskRequest

log = structlog.get_logger(__name__)


# ── Dangerous patterns: commands that are ALWAYS blocked ─────────────────────

DANGEROUS_COMMANDS = [
    # Disk destruction
    re.compile(r"\bformat\s+\w:[/\\]?", re.IGNORECASE),
    re.compile(r"\b(rd|rmdir)\s+[/\\][sqf].*", re.IGNORECASE),
    re.compile(r"\b(del|erase|rm)\s+/[sqf].*", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+[/\\]?(/|\*|\.\*)", re.IGNORECASE),
    re.compile(r"\bcipher\s+/w:", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    # System destruction
    re.compile(r"\bshutdown\s+.*/t\s+0", re.IGNORECASE),
    re.compile(r"\brestart-computer\b", re.IGNORECASE),
    re.compile(r"\bstop-computer\b", re.IGNORECASE),
    re.compile(r"\bbcdedit\s+/delete", re.IGNORECASE),
    # Security disabling
    re.compile(r"\b(disable|stop)\s+.*(antivirus|firewall|defender|防护)", re.IGNORECASE),
    re.compile(r"\bset-executionpolicy\s+bypass", re.IGNORECASE),
    re.compile(r"\bnetsh\s+.*firewall\s+set\s+opmode\s+disable", re.IGNORECASE),
    # Privilege escalation
    re.compile(r"\btakeown\s+/f\s+[A-Z]:[/\\]windows", re.IGNORECASE),
    re.compile(r"\bicacls\s+.*/grant\s+.*:F", re.IGNORECASE),
    re.compile(r"\breg\s+add\s+.*HKLM.*/f", re.IGNORECASE),
    re.compile(r"\breg\s+delete\s+.*HKLM", re.IGNORECASE),
    # Remote execution (piped)
    re.compile(r"(curl|wget)\s+.*\|\s*(bash|sh|powershell|cmd)", re.IGNORECASE),
    re.compile(r"(irm|iwr)\s+.*\|\s*(iex|invoke-expression)", re.IGNORECASE),
    # Dangerous git
    re.compile(r"\bgit\s+push\s+--force\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\s+--force-with-lease\s+origin\s+main", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\s+HEAD~", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-f[d]?[d]?\s*-", re.IGNORECASE),
]

# ── High-risk patterns: require explicit user confirmation ───────────────────

HIGH_RISK_PATTERNS = [
    # File deletion
    re.compile(r"\b(del|erase|rm|delete|remove)\s+", re.IGNORECASE),
    re.compile(r"\brmdir\b", re.IGNORECASE),
    # System commands
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\brunas\b", re.IGNORECASE),
    re.compile(r"\badmin\w*\b", re.IGNORECASE),
    # Registry
    re.compile(r"\breg\s+(add|delete|copy|import|export)", re.IGNORECASE),
    # Git destructive
    re.compile(r"\bgit\s+push\s+--force", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard", re.IGNORECASE),
    # Mass file operations
    re.compile(r"rm\s+-[rf]", re.IGNORECASE),
    re.compile(r"copy\s+.*\*\..*", re.IGNORECASE),
    re.compile(r"move\s+.*\*\..*", re.IGNORECASE),
    # Package management (can modify system)
    re.compile(r"\b(pip|npm|gem|cargo)\s+(install|uninstall|remove)\s+-g\b", re.IGNORECASE),
]

# Patterns that indicate high-risk shell commands
_DANGEROUS_SHELL_KEYWORDS = (
    "format",
    "diskpart",
    "cipher /w",
    "bcdedit /delete",
    "takeown /f",
    "icacls /grant",
    "set-executionpolicy",
    "disable antivirus",
    "disable firewall",
    "netsh firewall set opmode disable",
    "reg delete hklm",
    "reg add hklm",
    "shutdown /t 0",
    "restart-computer",
    "stop-computer",
    "rm -rf /",
    "del /s /q",
    "rmdir /s /q",
)


class SecurityAgent(BaseAgent):
    """
    Evaluates each task before execution:
      1. Regex-based dangerous command detection (always blocked)
      2. High-risk pattern detection (requires confirmation)
      3. Rate limiting
      4. Consent verification for destructive ops
      5. Audit logging
      6. App-Fencing -- once a task scope is set, only allow actions on in-scope windows

    Publishes security.violation on denial.
    """

    def __init__(self, ctx: AgentContext) -> None:
        super().__init__(ctx)
        self._action_times: deque[float] = deque()
        self._violation_count = 0
        self._allowed_apps: list[str] = []  # empty = no fencing (allow all)
        self._policy = SecurityPolicy(getattr(ctx.config.security, "allowed_fs_dirs", []))

    @property
    def name(self) -> str:
        return "security"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [AgentCapability("check_task", "Evaluate task safety before execution")]

    def check_shell_safety(self, command: str) -> tuple[bool, str]:
        """
        Check a shell command for dangerous patterns.

        Returns (is_safe, reason_string).
        If is_safe is False, the command should be blocked.
        """
        for pattern in DANGEROUS_COMMANDS:
            if pattern.search(command):
                return False, f"Dangerous command blocked: matches '{pattern.pattern[:50]}'"
        return True, ""

    def assess_shell_risk_level(self, command: str) -> str:
        """Assess shell command risk: critical, high, medium, or low."""
        cmd_lower = command.lower()

        for pattern in DANGEROUS_COMMANDS:
            if pattern.search(cmd_lower):
                return "critical"

        for pattern in HIGH_RISK_PATTERNS:
            if pattern.search(cmd_lower):
                return "high"

        # Check for shell interpreters (increases risk)
        if any(kw in cmd_lower for kw in ("powershell", "cmd.exe", "bash ", "wsl")):
            return "medium"

        return "low"

    async def check_task(self, request: TaskRequest) -> bool:
        desc = request.description.lower()

        # Check for dangerous patterns in the task description
        for pattern in DANGEROUS_COMMANDS:
            if pattern.search(desc):
                await self._record_violation(
                    request.id,
                    f"Dangerous command blocked: '{pattern.pattern[:60]}'",
                )
                return False

        # Rate limiting
        if not self._check_rate_limit():
            await self._record_violation(request.id, "Rate limit exceeded (60 req/min)")
            return False

        risk = self._assess_risk(desc)
        log.info("security.check", task_id=request.id, risk=risk)

        await self.ctx.audit.log_event(
            "task_check",
            {
                "task_id": request.id,
                "description": request.description[:150],
                "risk": risk,
                "decision": "allowed",
            },
        )

        return True

    def _assess_risk(self, description: str) -> str:
        """Assess risk of a task description."""
        # Check dangerous keywords
        for kw in _DANGEROUS_SHELL_KEYWORDS:
            if kw in description:
                return "high"

        # Check high-risk patterns
        for pattern in HIGH_RISK_PATTERNS:
            if pattern.search(description):
                return "high"

        # Check for shell execution
        if any(kw in description for kw in ("powershell", "cmd.exe", "bash", "terminal", "shell")):
            return "medium"

        # Check file operations
        if any(
            kw in description for kw in ("write file", "save", "delete file", "move file", "copy")
        ):
            return "medium"

        return "low"

    def _check_rate_limit(self) -> bool:
        now = time.monotonic()
        self._action_times = deque(t for t in self._action_times if now - t < 60.0)
        limit = getattr(self.config.security, "max_actions_per_minute", 60)
        if len(self._action_times) >= limit:
            return False
        self._action_times.append(now)
        return True

    # ──────────────────────────────────────────────────────────────
    # App-Fencing (Task Scope)
    # ──────────────────────────────────────────────────────────────

    def set_task_scope(self, allowed_apps: list[str]) -> None:
        """
        Restrict UI actions to windows/processes whose title or exe name
        contains one of the strings in `allowed_apps`.
        Call with an empty list to lift all restrictions.
        """
        self._allowed_apps = [a.lower() for a in allowed_apps]
        log.info("security.scope_set", allowed=self._allowed_apps)

    def clear_task_scope(self) -> None:
        self._allowed_apps = []

    async def check_action_window(self) -> bool:
        """
        Return True if the current foreground window is within the task scope.
        Always returns True when no scope is set.
        """
        if not self._allowed_apps:
            return True

        try:
            import win32gui
            import win32process

            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd).lower()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            proc_name = ""
            try:
                import psutil

                proc_name = psutil.Process(pid).name().lower()
            except Exception:
                pass

            for allowed in self._allowed_apps:
                if allowed in title or (proc_name and allowed in proc_name):
                    return True

            log.warning(
                "security.scope_violation", title=title, proc=proc_name, allowed=self._allowed_apps
            )
            await self.emit(
                "security.scope_violation",
                {
                    "window": title,
                    "process": proc_name,
                    "allowed": self._allowed_apps,
                },
                priority=1,
            )
            return False

        except Exception as exc:
            log.warning("security.scope_check_failed", error=str(exc))
            await self.emit(
                "security.scope_violation",
                {
                    "window": "",
                    "process": "",
                    "allowed": self._allowed_apps,
                    "reason": "scope_check_unavailable",
                },
                priority=1,
            )
            return False

    async def _record_violation(self, task_id: str, reason: str) -> None:
        self._violation_count += 1
        log.warning("security.violation", task_id=task_id, reason=reason)
        await self.ctx.audit.log_event(
            "security_violation",
            {
                "task_id": task_id,
                "reason": reason,
                "violation_count": self._violation_count,
            },
        )
        await self.emit("security.violation", {"task_id": task_id, "reason": reason}, priority=1)
