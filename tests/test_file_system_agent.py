"""Tests for FileSystemAgent sandbox enforcement."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents.file_system_agent import FileSystemAgent, _is_safe_path

# ── Helpers ────────────────────────────────────────────────────────────────────


def make_ctx(safe_dirs: list[str] | None = None):
    ctx = MagicMock()
    ctx.config.security.allowed_fs_dirs = safe_dirs or []
    ctx.audit.log_event = AsyncMock()
    ctx.bus.publish = AsyncMock()
    return ctx


# ── _is_safe_path unit tests ──────────────────────────────────────────────────


class TestIsSafePath:
    def test_child_is_safe(self, tmp_path: Path):
        child = tmp_path / "docs" / "file.txt"
        assert _is_safe_path(child, [tmp_path]) is True

    def test_parent_escape_blocked(self, tmp_path: Path):
        escaped = tmp_path / ".." / "etc" / "passwd"
        assert _is_safe_path(escaped, [tmp_path]) is False

    def test_sibling_blocked(self, tmp_path: Path):
        sibling = tmp_path.parent / "other_dir" / "file.txt"
        assert _is_safe_path(sibling, [tmp_path]) is False

    def test_exact_root_is_safe(self, tmp_path: Path):
        assert _is_safe_path(tmp_path, [tmp_path]) is True


# ── FileSystemAgent integration tests ─────────────────────────────────────────


class TestFileSystemAgentSandbox:
    @pytest.fixture
    def sandbox(self, tmp_path: Path):
        return tmp_path

    @pytest.fixture
    def agent(self, sandbox: Path):
        ctx = make_ctx(safe_dirs=[str(sandbox)])
        agent = FileSystemAgent(ctx)
        agent.bus = MagicMock()
        agent.bus.publish = AsyncMock()
        return agent

    @pytest.mark.asyncio
    async def test_read_inside_sandbox(self, agent, sandbox):
        test_file = sandbox / "hello.txt"
        test_file.write_text("hello world")
        result = await agent.read_file({"path": str(test_file)})
        assert result.success is True
        assert result.data["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_read_outside_sandbox_blocked(self, agent):
        result = await agent.read_file({"path": "C:/Windows/System32/drivers/etc/hosts"})
        assert result.success is False
        assert "outside allowed" in result.error

    @pytest.mark.asyncio
    async def test_write_inside_sandbox(self, agent, sandbox):
        out = sandbox / "output.txt"
        result = await agent.write_file({"path": str(out), "content": "data"})
        assert result.success is True
        assert out.read_text() == "data"

    @pytest.mark.asyncio
    async def test_write_outside_sandbox_blocked(self, agent):
        result = await agent.write_file({"path": "C:/Windows/evil.bat", "content": "rm -rf /"})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_delete_inside_sandbox(self, agent, sandbox):
        target = sandbox / "to_delete.txt"
        target.write_text("bye")
        result = await agent.delete_file({"path": str(target)})
        assert result.success is True
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_delete_outside_sandbox_blocked(self, agent):
        result = await agent.delete_file({"path": "C:/Windows/System32"})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, agent, sandbox):
        traversal = str(sandbox / ".." / ".." / "Windows" / "system.ini")
        result = await agent.read_file({"path": traversal})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_list_dir_inside_sandbox(self, agent, sandbox):
        (sandbox / "a.txt").write_text("a")
        (sandbox / "b.txt").write_text("b")
        result = await agent.list_dir({"path": str(sandbox)})
        assert result.success is True
        names = [i["name"] for i in result.data["items"]]
        assert "a.txt" in names and "b.txt" in names
