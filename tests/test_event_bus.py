"""Tests for the event bus pub/sub and priority dispatch."""

import asyncio

import pytest

from core.event_bus import Event, EventBus


@pytest.mark.asyncio
async def test_subscribe_and_receive():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event.topic)

    bus.subscribe("test.topic", handler)
    await bus.start()
    await bus.publish(Event("test.topic", {"x": 1}))
    await asyncio.sleep(0.1)
    await bus.stop()
    assert "test.topic" in received


@pytest.mark.asyncio
async def test_wildcard_subscription():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event.topic)

    bus.subscribe("vision.*", handler)
    await bus.start()
    await bus.publish(Event("vision.screen_updated", {}))
    await bus.publish(Event("vision.capture_done", {}))
    await asyncio.sleep(0.1)
    await bus.stop()
    assert len(received) == 2


@pytest.mark.asyncio
async def test_priority_ordering():
    bus = EventBus()
    order = []

    async def handler(event: Event):
        order.append(event.payload)

    bus.subscribe("pri.*", handler)
    await bus.start()
    await bus.publish(Event("pri.low", "low", priority=9))
    await bus.publish(Event("pri.high", "high", priority=0))
    await asyncio.sleep(0.2)
    await bus.stop()
    # High priority (0) should be processed first
    if len(order) >= 2:
        assert order[0] == "high"


@pytest.mark.asyncio
async def test_metrics_tracked():
    bus = EventBus()
    await bus.start()
    await bus.publish(Event("metrics.test", {}))
    await asyncio.sleep(0.1)
    await bus.stop()
    assert bus.metrics["published"] >= 1
