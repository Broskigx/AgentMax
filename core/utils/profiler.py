"""
Lightweight span profiler for the vision pipeline.

Usage
-----
# As a context manager:
with span("ocr.recognize"):
    results = ocr.recognize(frame)

# As a decorator:
@profile("vision.capture")
async def capture_screen(): ...

# Accumulated stats (reset per task):
profiler = PipelineProfiler()
with profiler.span("capture"):   capture()
with profiler.span("dedup"):     dedup()
with profiler.span("ocr"):       ocr()
report = profiler.report()       # {"capture": {"calls":1, "ms":12.3, ...}, ...}
"""

from __future__ import annotations

import functools
import time
from collections import defaultdict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Only log spans slower than this (ms). Set to 0 to log everything.
_LOG_THRESHOLD_MS = 5.0


@contextmanager
def span(name: str, threshold_ms: float = _LOG_THRESHOLD_MS) -> Generator[None, None, None]:
    """
    Lightweight context manager that logs elapsed time for a code block.
    Only emits a log line if the block takes longer than `threshold_ms`.
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000
        if ms >= threshold_ms:
            log.debug("span", name=name, ms=f"{ms:.1f}")


def profile(name: str, threshold_ms: float = _LOG_THRESHOLD_MS) -> Callable:
    """Decorator version of `span` for both sync and async functions."""

    def decorator(fn: Callable) -> Callable:
        if _is_async(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                t0 = time.perf_counter()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    ms = (time.perf_counter() - t0) * 1000
                    if ms >= threshold_ms:
                        log.debug("span", name=name, ms=f"{ms:.1f}")

            return async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                t0 = time.perf_counter()
                try:
                    return fn(*args, **kwargs)
                finally:
                    ms = (time.perf_counter() - t0) * 1000
                    if ms >= threshold_ms:
                        log.debug("span", name=name, ms=f"{ms:.1f}")

            return sync_wrapper

    return decorator


def _is_async(fn: Callable) -> bool:
    import asyncio

    return asyncio.iscoroutinefunction(fn)


# ── Accumulated stats per task ─────────────────────────────────────────────────


class PipelineProfiler:
    """
    Collects span timings within a single task execution and reports
    per-stage latency statistics (min/avg/max, call count).

    Create one per task in the Supervisor, pass it down to vision agents,
    and call `report()` when the task finishes.
    """

    def __init__(self, task_id: str = "") -> None:
        self.task_id = task_id
        self._data: dict[str, list[float]] = defaultdict(list)

    @contextmanager
    def span(self, name: str) -> Generator[None, None, None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            ms = (time.perf_counter() - t0) * 1000
            self._data[name].append(ms)

    def record(self, name: str, ms: float) -> None:
        """Manually record a pre-measured span."""
        self._data[name].append(ms)

    def report(self) -> dict[str, dict[str, float]]:
        """
        Returns a dict keyed by span name.
        Each value: {calls, total_ms, min_ms, avg_ms, max_ms, p95_ms}.
        """
        result: dict[str, dict[str, float]] = {}
        for name, samples in self._data.items():
            if not samples:
                continue
            sorted_s = sorted(samples)
            n = len(sorted_s)
            result[name] = {
                "calls": float(n),
                "total_ms": sum(sorted_s),
                "min_ms": sorted_s[0],
                "avg_ms": sum(sorted_s) / n,
                "max_ms": sorted_s[-1],
                "p95_ms": sorted_s[int(n * 0.95)],
            }
        return result

    def log_report(self) -> None:
        report = self.report()
        if not report:
            return
        total = sum(v["total_ms"] for v in report.values())
        log.info(
            "pipeline.profile",
            task_id=self.task_id,
            total_ms=f"{total:.1f}",
            stages={k: f"{v['avg_ms']:.1f}ms avg" for k, v in report.items()},
        )
