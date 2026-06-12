"""Serialized tool queue with bounded concurrency."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class ToolQueue:
    def __init__(self, max_concurrent: int = 2) -> None:
        self._global = asyncio.Semaphore(max(1, max_concurrent))
        self._serialized = asyncio.Lock()
        self._pending = 0

    @property
    def pending(self) -> int:
        return self._pending

    async def run(self, mode: str, work: Callable[[], Awaitable[T]]) -> T:
        self._pending += 1
        try:
            if mode == "serialized":
                async with self._serialized:
                    return await work()
            async with self._global:
                return await work()
        finally:
            self._pending = max(0, self._pending - 1)
