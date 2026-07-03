"""Permission manager -- enforces allowlists, blocklists, and consent requirements."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class PermissionDeniedError(PermissionError):
    """Raised when a required permission has not been granted."""

    def __init__(self, permission: str) -> None:
        super().__init__(f"Permission '{permission}' has not been granted")
        self.permission = permission


@dataclass(slots=True)
class PermissionGrant:
    permission: str
    task_id: str | None = None
    session_id: str | None = None
    expires_at: float | None = None

    def active(self, now: float) -> bool:
        return self.expires_at is None or self.expires_at > now


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

    DEFAULT_GRANTED: set[str] = set()

    def __init__(self, config: Any) -> None:
        self._config = config
        self._granted: set[str] = set(self.DEFAULT_GRANTED)
        self._scoped_grants: list[PermissionGrant] = []
        self._denied: set[str] = set()
        self._pending: dict[str, asyncio.Event] = {}

    def grant(
        self,
        permission: str,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
        ttl_sec: float | None = None,
    ) -> None:
        self._validate_name(permission)
        if task_id or session_id or ttl_sec is not None:
            expires_at = time.monotonic() + max(0.0, ttl_sec) if ttl_sec is not None else None
            self._scoped_grants.append(PermissionGrant(permission, task_id, session_id, expires_at))
        else:
            self._granted.add(permission)
        self._denied.discard(permission)
        log.info(
            "permission.granted",
            permission=permission,
            task_id=task_id,
            session_id=session_id,
            ttl_sec=ttl_sec,
        )

    def deny(self, permission: str) -> None:
        self._denied.add(permission)
        self._granted.discard(permission)
        log.info("permission.denied", permission=permission)

    def has(
        self,
        permission: str,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        if permission in self._denied:
            return False
        if permission in self._granted:
            return True
        now = time.monotonic()
        self._scoped_grants = [grant for grant in self._scoped_grants if grant.active(now)]
        return any(
            grant.permission == permission
            and (grant.task_id is None or grant.task_id == task_id)
            and (grant.session_id is None or grant.session_id == session_id)
            for grant in self._scoped_grants
        )

    async def require(
        self,
        permission: str,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Ensure a permission is granted, raising PermissionDeniedError if not.

        In development mode (require_consent=False) permissions are auto-granted.
        In production, raises PermissionDeniedError if not already granted.
        """
        if self.has(permission, task_id=task_id, session_id=session_id):
            return
        require_consent: bool = getattr(self._config, "require_consent", True)
        if not require_consent:
            self.grant(permission, task_id=task_id, session_id=session_id)
            return
        log.warning("permission.denied_at_require", permission=permission)
        raise PermissionDeniedError(permission)

    def check(
        self,
        permission: str,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        """Non-raising check -- use when you want to branch on permission state."""
        return self.has(permission, task_id=task_id, session_id=session_id)

    def revoke_scope(
        self,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._scoped_grants = [
            grant
            for grant in self._scoped_grants
            if not (
                (task_id is not None and grant.task_id == task_id)
                or (session_id is not None and grant.session_id == session_id)
            )
        ]

    def _validate_name(self, permission: str) -> None:
        if permission not in self.ALL_PERMISSIONS:
            raise ValueError(f"Unknown permission: {permission}")

    @property
    def granted_permissions(self) -> list[str]:
        return sorted(self._granted)

    @property
    def denied_permissions(self) -> list[str]:
        return sorted(self._denied)
