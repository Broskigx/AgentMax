"""AgentMax security subpackage. Public API surface."""

from __future__ import annotations

from . import ipc_auth
from .audit_log import AuditLog
from .permission_manager import PermissionManager
from .token_manager import (
    TokenConfig,
    TokenLimitError,
    TokenManager,
    TokenUsage,
    UnlimitedTokenError,
    create_token_manager,
)

__all__ = [
    "AuditLog",
    "PermissionManager",
    "TokenManager",
    "TokenConfig",
    "TokenUsage",
    "TokenLimitError",
    "UnlimitedTokenError",
    "create_token_manager",
    "ipc_auth",
]
