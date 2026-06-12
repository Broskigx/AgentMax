"""
FileSystemAgent -- sandboxed file and directory manipulation.

Security: all paths are resolved and validated against an allowlist of safe
directories before any I/O is performed. Attempts to escape the sandbox via
path traversal (../../etc/passwd, symlinks, etc.) are blocked and audited.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

from core.agents.base_agent import ActionResult, AgentCapability, AgentContext, BaseAgent

log = structlog.get_logger(__name__)

# Default safe directories -- user home sub-dirs only.
_DEFAULT_SAFE_DIRS: list[Path] = [
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "AppData" / "Local" / "AgentMax",
]


def _resolve_safe_dirs(config_dirs: list[str]) -> list[Path]:
    if config_dirs:
        return [Path(d).resolve() for d in config_dirs]
    return [d.resolve() for d in _DEFAULT_SAFE_DIRS]


def _is_safe_path(path: Path, safe_dirs: list[Path]) -> bool:
    """Return True only if `path` resolves inside one of the safe directories."""
    try:
        resolved = path.resolve()
        return any(resolved == safe or safe in resolved.parents for safe in safe_dirs)
    except Exception:
        return False


class FileSystemAgent(BaseAgent):
    """
    Handles direct disk I/O within a sandboxed directory allowlist.
    Every operation is audit-logged. Unsafe paths are rejected before I/O.
    """

    def __init__(self, ctx: AgentContext) -> None:
        super().__init__(ctx)
        cfg_dirs: list[str] = getattr(ctx.config.security, "allowed_fs_dirs", [])
        self._safe_dirs = _resolve_safe_dirs(cfg_dirs)

    @property
    def name(self) -> str:
        return "file_system"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [
            AgentCapability("read_file", "Read content of a file"),
            AgentCapability("write_file", "Create or overwrite a file"),
            AgentCapability("list_dir", "List contents of a directory"),
            AgentCapability("move_file", "Move or rename a file/folder"),
            AgentCapability("delete_file", "Permanently delete a file/folder"),
        ]

    def _check_path(self, path: Path) -> ActionResult | None:
        """Return an ActionResult error if path is outside the sandbox, else None."""
        if not _is_safe_path(path, self._safe_dirs):
            safe_list = [str(d) for d in self._safe_dirs]
            msg = f"Path '{path}' is outside allowed directories: {safe_list}"
            log.warning("fs_agent.sandbox_violation", path=str(path), allowed=safe_list)
            return ActionResult(success=False, error=msg)
        return None

    async def _audit(self, op: str, path: str, extra: dict | None = None) -> None:
        await self.ctx.audit.log_event(f"fs.{op}", {"path": path, **(extra or {})})

    async def read_file(self, step: dict) -> ActionResult:
        path = Path(step.get("path", ""))
        if err := self._check_path(path):
            await self._audit("read_blocked", str(path))
            return err
        try:
            if not path.exists():
                return ActionResult(success=False, error=f"File not found: {path}")
            content = path.read_text(encoding="utf-8", errors="replace")
            await self._audit("read", str(path), {"size": len(content)})
            await self.log_terminal(f"Read: {path.name} ({len(content):,} bytes)", "stdout")
            return ActionResult(success=True, data={"content": content})
        except Exception as exc:
            return ActionResult(success=False, error=str(exc))

    async def write_file(self, step: dict) -> ActionResult:
        path = Path(step.get("path", ""))
        if err := self._check_path(path):
            await self._audit("write_blocked", str(path))
            return err
        content = step.get("content", "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            await self._audit("write", str(path), {"size": len(content)})
            await self.log_terminal(f"Written: {path.name}", "info")
            return ActionResult(success=True)
        except Exception as exc:
            return ActionResult(success=False, error=str(exc))

    async def list_dir(self, step: dict) -> ActionResult:
        path = Path(step.get("path", "."))
        if err := self._check_path(path):
            await self._audit("list_blocked", str(path))
            return err
        try:
            items = [
                {
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                }
                for item in path.iterdir()
            ]
            await self._audit("list", str(path), {"count": len(items)})
            await self.log_terminal(f"Listed: {path} ({len(items)} items)", "stdout")
            return ActionResult(success=True, data={"items": items})
        except Exception as exc:
            return ActionResult(success=False, error=str(exc))

    async def move_file(self, step: dict) -> ActionResult:
        src = Path(step.get("source", ""))
        dst = Path(step.get("destination", ""))
        if err := self._check_path(src):
            await self._audit("move_blocked", str(src))
            return err
        if err := self._check_path(dst):
            await self._audit("move_blocked_dst", str(dst))
            return err
        try:
            shutil.move(str(src), str(dst))
            await self._audit("move", str(src), {"destination": str(dst)})
            await self.log_terminal(f"Moved: {src.name} -> {dst.name}", "info")
            return ActionResult(success=True)
        except Exception as exc:
            return ActionResult(success=False, error=str(exc))

    async def delete_file(self, step: dict) -> ActionResult:
        path = Path(step.get("path", ""))
        if err := self._check_path(path):
            await self._audit("delete_blocked", str(path))
            return err
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            await self._audit("delete", str(path))
            await self.log_terminal(f"DELETED: {path.name}", "stderr")
            return ActionResult(success=True)
        except Exception as exc:
            return ActionResult(success=False, error=str(exc))
