"""Tests for security agent permission checks."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents.security_agent import SecurityAgent


def make_ctx():
    ctx = MagicMock()
    ctx.config.security.max_actions_per_minute = 120
    ctx.config.security.require_consent = True
    ctx.audit.log_event = AsyncMock()
    return ctx


def make_request(description: str):
    req = MagicMock()
    req.id = "test-001"
    req.description = description
    return req


class TestSecurityAgent:
    @pytest.mark.asyncio
    async def test_safe_task_allowed(self):
        agent = SecurityAgent(make_ctx())
        agent.bus = MagicMock()
        agent.bus.publish = AsyncMock()
        result = await agent.check_task(make_request("Open Chrome and go to google.com"))
        assert result is True

    @pytest.mark.asyncio
    async def test_dangerous_task_blocked(self):
        agent = SecurityAgent(make_ctx())
        agent.bus = MagicMock()
        agent.bus.publish = AsyncMock()
        result = await agent.check_task(make_request("format C: /fs:ntfs"))
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_system_blocked(self):
        agent = SecurityAgent(make_ctx())
        agent.bus = MagicMock()
        agent.bus.publish = AsyncMock()
        result = await agent.check_task(make_request("rm -rf /"))
        assert result is False

    @pytest.mark.asyncio
    async def test_rate_limit_enforced(self):
        ctx = make_ctx()
        ctx.config.security.max_actions_per_minute = 2
        agent = SecurityAgent(ctx)
        agent.bus = MagicMock()
        agent.bus.publish = AsyncMock()

        req = make_request("open notepad")
        r1 = await agent.check_task(req)
        r2 = await agent.check_task(req)
        r3 = await agent.check_task(req)  # should be rate-limited
        assert r1 is True
        assert r2 is True
        assert r3 is False
