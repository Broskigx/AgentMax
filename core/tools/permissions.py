"""Bridge canonical tool scopes to scoped runtime permissions."""

from __future__ import annotations

from core.tools.models import ToolDefinition, ToolExecutionContext, ToolRequest, ValidationReport

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

_SCOPE_MAP: dict[tuple[str, str], str] = {
    ("vision", "read"): "SCREEN_READ",
    ("screen", "read"): "SCREEN_READ",
    ("window", "read"): "SCREEN_READ",
    ("filesystem", "read"): "FILE_READ",
    ("filesystem", "list"): "FILE_READ",
    ("filesystem", "write"): "FILE_WRITE",
    ("filesystem", "destructive"): "FILE_WRITE",
    ("mouse", "input"): "INPUT_MOUSE",
    ("keyboard", "input"): "INPUT_KEYBOARD",
    ("app", "input"): "INPUT_MOUSE",
    ("app", "read"): "SCREEN_READ",
    ("browser", "network"): "NETWORK",
    ("network", "network"): "NETWORK",
    ("shell", "elevated"): "PROCESS_LAUNCH",
    ("shell", "input"): "PROCESS_LAUNCH",
    ("process", "elevated"): "PROCESS_LAUNCH",
    ("process", "destructive"): "PROCESS_LAUNCH",
}


def _computer_permissions(request: ToolRequest) -> list[str]:
    permissions: list[str] = []
    actions = request.input.get("actions", [])
    for action in actions if isinstance(actions, list) else []:
        action_type = str((action or {}).get("action") or "").lower()
        if action_type in {"click", "double_click", "right_click", "move", "scroll", "drag"}:
            permissions.extend(("INPUT_MOUSE", "SCREEN_READ"))
        elif action_type in {"type", "key", "hotkey", "press"}:
            permissions.extend(("INPUT_KEYBOARD", "SCREEN_READ"))
        elif action_type == "screenshot":
            permissions.append("SCREEN_READ")
    return list(dict.fromkeys(permissions))


def _resolve_permission(scope: str, category: str) -> str | None:
    return _PERMISSION_MAP.get(scope) or _SCOPE_MAP.get((category, scope))


class ToolPermissionManager:
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
        resolved: list[tuple[str, str | None]]
        if definition.id == "computer.execute":
            resolved = [
                (permission.lower(), permission)
                for permission in _computer_permissions(request)
            ]
        else:
            resolved = [
                (scope, _resolve_permission(scope, definition.category))
                for scope in definition.permissions
            ]
            if definition.id.startswith(("mouse.", "keyboard.", "app.", "ui.")):
                resolved.append(("screen.read", "SCREEN_READ"))
        resolved = list(dict.fromkeys(resolved))
        for scope, runtime_permission in resolved:
            if not runtime_permission:
                report.add(
                    "permission.unknown",
                    f"Unknown permission {scope!r} for category "
                    f"{definition.category!r} (tool {definition.id})",
                    scope,
                )
                continue
            if not manager or not hasattr(manager, "check"):
                report.add(
                    "permission.unavailable",
                    f"Permission manager unavailable for {runtime_permission}",
                    scope,
                )
                continue
            if not manager.check(
                runtime_permission,
                task_id=context.task_id,
                session_id=context.extra.get("session_id"),
            ):
                report.add(
                    "permission.required",
                    f"Permission {runtime_permission} is not granted",
                    scope,
                )
        return report


__all__ = ["ToolPermissionManager"]
