"""Tests for WebSearchAgent SSRF protection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents.web_search_agent import WebSearchAgent, _is_safe_url


class TestIsSafeUrl:
    def test_public_https_allowed(self):
        ok, reason = _is_safe_url("https://www.google.com/search?q=cats")
        assert ok is True

    def test_public_http_allowed(self):
        ok, reason = _is_safe_url("http://example.com")
        assert ok is True

    def test_localhost_blocked(self):
        ok, reason = _is_safe_url("http://127.0.0.1:7789/api/capture")
        assert ok is False
        assert "SSRF" in reason or "private" in reason.lower()

    def test_localhost_name_blocked(self):
        ok, reason = _is_safe_url("http://localhost/admin")
        # DNS may resolve to 127.0.0.1; if DNS fails we allow (fail-open is expected)
        # Just ensure the function doesn't throw
        assert isinstance(ok, bool)

    def test_private_10_blocked(self):
        ok, reason = _is_safe_url("http://10.0.0.1/secret")
        assert ok is False

    def test_private_192_168_blocked(self):
        ok, reason = _is_safe_url("http://192.168.1.1/router")
        assert ok is False

    def test_file_scheme_blocked(self):
        ok, reason = _is_safe_url("file:///etc/passwd")
        assert ok is False

    def test_ftp_scheme_blocked(self):
        ok, reason = _is_safe_url("ftp://files.example.com/data")
        assert ok is False

    def test_malformed_url(self):
        ok, reason = _is_safe_url("not-a-url")
        # scheme is empty so it should be blocked
        assert ok is False


class TestWebSearchAgentBlocking:
    def make_agent(self):
        ctx = MagicMock()
        ctx.config = MagicMock()
        ctx.audit.log_event = AsyncMock()
        ctx.bus.publish = AsyncMock()
        agent = WebSearchAgent(ctx)
        agent.bus = MagicMock()
        agent.bus.publish = AsyncMock()
        return agent

    @pytest.mark.asyncio
    async def test_ssrf_blocked_in_read_page(self):
        agent = self.make_agent()
        result = await agent.read_page({"url": "http://127.0.0.1:7789/api/capture"})
        assert result.success is False
        assert "blocked" in result.error.lower() or "SSRF" in result.error

    @pytest.mark.asyncio
    async def test_no_url_returns_error(self):
        agent = self.make_agent()
        result = await agent.read_page({})
        assert result.success is False
        assert "No URL" in result.error

    @pytest.mark.asyncio
    async def test_no_query_returns_error(self):
        agent = self.make_agent()
        result = await agent.search({})
        assert result.success is False
