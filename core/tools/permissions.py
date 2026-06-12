"""Tool permission checks.

Two naming schemes for tool permissions coexist in the codebase:

1. **Dot-namespaced** (legacy, used by hand-written code paths):
   ``input.mouse``, ``screen.read``, ``filesystem.write``, ...

2. **Short scopes** (used in ``core/ai/tools.json``):
   ``input``, ``read``, ``write``, ``network``, ``destructive``, ``elevated``.

The short scopes are ambiguous on their own — ``read`` means screen-read for a
vision tool but file-read for a filesystem tool — so they need to be resolved
against the tool's ``category``. This module accepts BOTH schemes and
disambiguates by category for the short ones.
"""

from __future__ import annotations

from core.tools.models import ToolDefinition, ToolExecutionContext, ToolRequest, ValidationReport

# Dot-namespaced (legacy): permission_name -> runtime permission
_PERMISSION_MAP: dict[str, str] = {
    "input.mouse": "INPUT_MOUSE",
    "input.keyboard": "INPUT_KEYBOARD",
    "screen.read": "SCREEN_READ",
    "process.launch": "PROCESS_LAUNCH",
    "process.manage": "PROCESS_LAUNCH",
    "filesystem.read": "FILE_READ",
    "filesystem.write": "FILE_WRITE",
    "network.http": "NETWORK",
    "shell.run": "PROCESS_LAUNCH",
    "memory.read": "SCREEN_READ",
    "memory.write": "SCREEN_READ",
}

# Short scope (from tools.json): (category, scope) -> runtime permission.
# Resolved against the tool's category when the permission name is a short
# scope and not already in _PERMISSION_MAP.
_SCOPE_MAP: dict[tuple[str, str], str] = {
    # Vision / screen
    ("vision", "read"): "SCREEN_READ",
    ("screen", "read"): "SCREEN_READ",
    # Filesystem
    ("filesystem", "read"): "FILE_READ",
    ("filesystem", "list"): "FILE_READ",
    ("filesystem", "write"): "FILE_WRITE",
    ("filesystem", "destructive"): "FILE_WRITE",
    # Input devices
    ("mouse", "input"): "INPUT_MOUSE",
    ("keyboard", "input"): "INPUT_KEYBOARD",
    # Network
    ("network", "network"): "NETWORK",
    ("browser", "network"): "NETWORK",
    # Shell / process
    ("shell", "elevated"): "PROCESS_LAUNCH",
    ("shell", "input"): "PROCESS_LAUNCH",
    ("process", "elevated"): "PROCESS_LAUNCH",
    ("app", "input"): "PROCESS_LAUNCH",
    # Catch-all destructive (filesystem covered above; this hits process delete)
    ("process", "destructive"): "PROCESS_LAUNCH",
}

# Permission names that bypass consent when the user has granted
# "computer control" in the UI. Mirrors the legacy dot-namespaced set + the
# short-scope equivalents resolved through _SCOPE_MAP.
_COMPUTER_CONTROL_BYPASS = frozenset(
    {
        "input.mouse",
        "input.keyboard",
        "screen.read",
        "process.launch",
        "process.manage",
        "filesystem.read",
        "filesystem.write",
        "network.http",
        # Short scopes — bypass when the tool is part of the computer-control
        # toolkit (mouse/keyboard/screenshot/list_dir/etc).
        "input",
        "read",
        "network",
    }
)


def _resolve_permission(scope: str, category: str) -> str | None:
    """Map a tool's declared permission to a runtime permission constant.

    Tries the dot-namespaced map first (so existing entries keep working),
    then falls back to (category, scope) resolution for the short scopes
    used in ``tools.json``.
    """
    direct = _PERMISSION_MAP.get(scope)
    if direct:
        return direct
    return _SCOPE_MAP.get((category, scope))


class ToolPermissionManager:
    """Bridge tool permissions to the existing runtime PermissionManager."""

    def validate(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
    ) -> ValidationReport:
        if request.dry_run:
            return ValidationReport.ok_report()

        report = ValidationReport.ok_report()
        manager = context.security
        for permission in definition.permissions:
            runtime_permission = _resolve_permission(permission, definition.category)
            if not runtime_permission:
                report.add(
                    "permission.unknown",
                    f"Unknown permission {permission!r} for category "
                    f"{definition.category!r} (tool {definition.id})",
                    permission,
                )
                continue
            if (
                context.extra.get("computer_control_granted")
                and permission in _COMPUTER_CONTROL_BYPASS
            ):
                continue
            if manager and hasattr(manager, "check"):
                if not manager.check(runtime_permission):
                    report.add(
                        "permission.denied",
                        f"Permission {runtime_permission} is not granted",
                        permission,
                    )
        return report


__all__ = ["ToolPermissionManager"]
