"""
Circuit breaker for AI API calls -- re-exports from unified resilience module.

States: CLOSED (normal) -> OPEN (blocking) -> HALF_OPEN (probe) -> CLOSED

Usage:
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    async with cb.guard("claude-api"):
        result = await client.call(...)
"""

from core.utils.resilience import (
    BreakerOpen as BreakerOpen,
    BreakerState as CBState,
    CircuitBreaker as CircuitBreaker,
    CircuitOpenError as CircuitOpenError,
    get_claude_circuit_breaker as get_claude_circuit_breaker,
)
