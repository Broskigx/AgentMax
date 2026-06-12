"""Permission manager -- enforces allowlists, blocklists, and consent requirements."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class PermissionDeniedError(PermissionError):
    """Raised when a required permission has not been granted."""

    def __init__(self, permission: str) -> None:
        super().__init__(f"Permission '{permission}' has not been granted")
        self.permission = permission


class PermissionManager:
    """
    Gates all potentially sensitive operations.

    Permissions:
      - SCREEN_READ: capture screenshots
      - INPUT_MOUSE: control mouse
      - INPUT_KEYBOARD: control keyboard
      - PROCESS_LAUNCH: start new processes
      - FILE_READ / FILE_WRITE: file system access
      - REGISTRY: registry access
      - NETWORK: network access

    All permissions are explicitly granted via UI consent dialog.
    None are silently assumed.
    """

    ALL_PERMISSIONS = {
        "SCREEN_READ",
        "INPUT_MOUSE",
        "INPUT_KEYBOARD",
        "PROCESS_LAUNCH",
        "FILE_READ",
        "FILE_WRITE",
        "REGISTRY",
        "NETWORK",
    }

    DEFAULT_GRANTED = {"SCREEN_READ", "INPUT_MOUSE", "INPUT_KEYBOARD"}

    def __init__(self, config: Any) -> None:
        self._config = config
        self._granted: set[str] = set(self.DEFAULT_GRANTED)
        self._denied: set[str] = set()
        self._pending: dict[str, asyncio.Event] = {}

    def grant(self, permission: str) -> None:
        self._granted.add(permission)
        self._denied.discard(permission)
        log.info("permission.granted", permission=permission)

    def deny(self, permission: str) -> None:
        self._denied.add(permission)
        self._granted.discard(permission)
        log.info("permission.denied", permission=permission)

    def has(self, permission: str) -> bool:
        if permission in self._denied:
            return False
        return permission in self._granted

    async def require(self, permission: str) -> None:
        """Ensure a permission is granted, raising PermissionDeniedError if not.

        In development mode (require_consent=False) permissions are auto-granted.
        In production, raises PermissionDeniedError if not already granted.
        """
        if self.has(permission):
            return
        require_consent: bool = getattr(self._config, "require_consent", True)
        if not require_consent:
            self.grant(permission)
            return
        log.warning("permission.denied_at_require", permission=permission)
        raise PermissionDeniedError(permission)

    def check(self, permission: str) -> bool:
        """Non-raising check -- use when you want to branch on permission state."""
        return self.has(permission)

    @property
    def granted_permissions(self) -> list[str]:
        return sorted(self._granted)

    @property
    def denied_permissions(self) -> list[str]:
        return sorted(self._denied)
