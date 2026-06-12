"""Tests for the AI circuit breaker."""

from __future__ import annotations

import asyncio

import pytest

from core.ai.circuit_breaker import CBState, CircuitBreaker, CircuitOpenError


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_closed_allows_calls(self):
        cb = CircuitBreaker(failure_threshold=3)
        async with await cb.guard("test"):
            pass  # should not raise
        assert cb.state == CBState.CLOSED

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=999)
        for _ in range(3):
            try:
                async with await cb.guard("test"):
                    raise ValueError("simulated failure")
            except ValueError:
                pass

        assert cb.state == CBState.OPEN

        with pytest.raises(CircuitOpenError):
            async with await cb.guard("test"):
                pass

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        # 2 failures
        for _ in range(2):
            try:
                async with await cb.guard("test"):
                    raise ValueError("fail")
            except ValueError:
                pass
        # 1 success -- should reset
        async with await cb.guard("test"):
            pass
        assert cb.state == CBState.CLOSED
        assert cb._failures == 0

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        try:
            async with await cb.guard("test"):
                raise RuntimeError("fail")
        except RuntimeError:
            pass
        assert cb.state == CBState.OPEN

        await asyncio.sleep(0.05)

        # Next call should be allowed (HALF_OPEN probe)
        async with await cb.guard("test"):
            pass
        assert cb.state == CBState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        try:
            async with await cb.guard("test"):
                raise RuntimeError("fail")
        except RuntimeError:
            pass

        await asyncio.sleep(0.05)

        # Probe fails -- circuit reopens
        try:
            async with await cb.guard("test"):
                raise RuntimeError("probe fail")
        except RuntimeError:
            pass

        assert cb.state == CBState.OPEN
