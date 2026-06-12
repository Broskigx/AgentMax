import pytest

from core.memory.memory_manager import MemoryImportance, MemoryManager, MemoryQuery, MemoryType


@pytest.mark.asyncio
async def test_contextual_memory_ranking_uses_tokens_tags_and_importance() -> None:
    manager = MemoryManager()
    await manager.store_memory(
        "User prefers compact terminal diagnostics and retry summaries.",
        memory_type=MemoryType.SEMANTIC,
        importance=MemoryImportance.HIGH,
        tags=["terminal", "preferences"],
        context_id="session-1",
    )
    await manager.store_memory(
        "Unrelated visual theme note.",
        memory_type=MemoryType.SEMANTIC,
        importance=MemoryImportance.LOW,
        tags=["theme"],
        context_id="session-1",
    )

    results = await manager.search_memories(
        MemoryQuery(query_text="terminal retry diagnostics", tags=["terminal"], limit=2)
    )

    assert results[0].memory.content.startswith("User prefers")
    assert results[0].score_breakdown["lexical"] > 0
    assert results[0].highlight


@pytest.mark.asyncio
async def test_memory_context_compression_and_cleanup() -> None:
    manager = MemoryManager()
    first = await manager.store_memory(
        "Critical production rule that should survive cleanup.",
        memory_type=MemoryType.SEMANTIC,
        importance=MemoryImportance.CRITICAL,
        tags=["policy"],
    )
    await manager.store_memory(
        "Temporary low value scratch memory about retry UI.",
        memory_type=MemoryType.SHORT_TERM,
        importance=MemoryImportance.LOW,
        tags=["scratch"],
    )

    compressed = await manager.compress_context("production policy retry", max_chars=180)
    cleanup = await manager.smart_cleanup(max_memories=1)

    assert "production" in compressed.lower()
    assert cleanup.deleted_count >= 1
    assert await manager.retrieve_memory(first.memory_id) is not None
