"""
AgentMax Memory System -- persistent memory with vector storage and semantic search.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

import structlog

log = structlog.get_logger(__name__)
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_áéíóúÁÉÍÓÚñÑ]{3,}")


class MemoryType(str, Enum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    WORKING = "working"
    PROCEDURAL = "procedural"


class MemoryImportance(str, Enum):
    LOW = 1
    MEDIUM = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class MemoryBlock:
    memory_id: str
    content: str
    memory_type: MemoryType
    created_at: datetime
    updated_at: datetime

    embedding: list[float] | None = None
    importance: MemoryImportance = MemoryImportance.MEDIUM
    access_count: int = 0
    last_accessed: datetime | None = None

    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    expires_at: datetime | None = None

    related_memories: list[str] = field(default_factory=list)
    parent_memory: str | None = None
    context_id: str | None = None
    session_id: str | None = None

    def is_expired(self) -> bool:
        if self.expires_at:
            return datetime.now(UTC) > self.expires_at
        return False

    def should_consolidate(self) -> bool:
        if self.memory_type != MemoryType.SHORT_TERM:
            return False
        # Critical memories consolidate immediately; others need repeated access.
        if self.importance == MemoryImportance.CRITICAL:
            return True
        return self.access_count > 5 and self.importance.value >= 5

    def get_importance_score(self) -> float:
        base = float(self.importance.value)
        if self.last_accessed:
            hours = (datetime.now(UTC) - self.last_accessed).total_seconds() / 3600
            if hours < 1:
                base *= 1.5
            elif hours < 24:
                base *= 1.2
        if self.access_count > 10:
            base *= 1.3
        if self.importance == MemoryImportance.CRITICAL:
            base *= 2.0
        return min(base, 100.0)

    def compact_summary(self, max_chars: int = 280) -> str:
        text = " ".join(self.content.split())
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        return text


@dataclass
class MemoryQuery:
    query_text: str
    query_embedding: list[float] | None = None
    memory_types: list[MemoryType] | None = None
    tags: list[str] | None = None
    min_importance: MemoryImportance | None = None
    limit: int = 10
    from_date: datetime | None = None
    to_date: datetime | None = None
    context_id: str | None = None
    session_id: str | None = None
    prefer_recent: bool = True


@dataclass
class MemorySearchResult:
    memory: MemoryBlock
    relevance_score: float
    highlight: str | None = None
    score_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass
class MemoryConsolidationResult:
    consolidated_count: int
    deleted_count: int
    updated_count: int
    errors: list[str] = field(default_factory=list)


@dataclass
class VectorStoreConfig:
    embedding_model: str = "sentence-transformers"
    embedding_dimensions: int = 384
    index_type: str = "flat"
    metric: str = "cosine"


class MemoryVectorStore:
    """Vector store using numpy for fast batch cosine similarity."""

    def __init__(self, config: VectorStoreConfig | None = None) -> None:
        self.config = config or VectorStoreConfig()
        self._index: dict[str, list[float]] = {}
        self._matrix: Any = None  # numpy array, rebuilt on demand
        self._matrix_ids: list[str] = []
        self._dirty = False

    async def add_vectors(self, memory_id: str, embedding: list[float]) -> None:
        self._index[memory_id] = embedding
        if memory_id not in self._matrix_ids:
            self._matrix_ids.append(memory_id)
        self._dirty = True

    async def search_similar(
        self,
        query_embedding: list[float],
        limit: int = 10,
        exclude_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        if not self._index:
            return []

        exclude = set(exclude_ids or [])
        candidates = [(mid, emb) for mid, emb in self._index.items() if mid not in exclude]
        if not candidates:
            return []

        try:
            import numpy as np

            ids = [c[0] for c in candidates]
            matrix = np.array([c[1] for c in candidates], dtype=np.float32)
            query = np.array(query_embedding, dtype=np.float32)

            # Batch cosine similarity: (matrix @ query) / (|matrix| * |query|)
            norms = np.linalg.norm(matrix, axis=1)
            query_norm = np.linalg.norm(query)
            norms = np.where(norms == 0, 1e-9, norms)
            query_norm = query_norm if query_norm != 0 else 1e-9

            similarities = (matrix @ query) / (norms * query_norm)
            top_k = min(limit, len(similarities))
            top_indices = np.argpartition(similarities, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

            return [(ids[i], float(similarities[i])) for i in top_indices]
        except ImportError:
            # Pure Python fallback (slower but dependency-free)
            results = [
                (mid, self._cosine_similarity(query_embedding, emb)) for mid, emb in candidates
            ]
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:limit]

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        dot = sum(a * b for a, b in zip(vec1, vec2, strict=False))
        mag1 = sum(a * a for a in vec1) ** 0.5
        mag2 = sum(b * b for b in vec2) ** 0.5
        return dot / (mag1 * mag2) if mag1 and mag2 else 0.0

    async def delete_vector(self, memory_id: str) -> None:
        self._index.pop(memory_id, None)
        if memory_id in self._matrix_ids:
            self._matrix_ids.remove(memory_id)
        self._dirty = True

    async def get_memory_count(self) -> int:
        return len(self._index)


class MemoryManager:
    """Main memory management system."""

    def __init__(
        self,
        vector_store: MemoryVectorStore | None = None,
        embedding_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self.vector_store = vector_store or MemoryVectorStore()
        self.embedding_fn = embedding_fn
        self._memories: dict[str, MemoryBlock] = {}
        self._type_index: dict[MemoryType, list[str]] = {t: [] for t in MemoryType}
        self._tag_index: dict[str, list[str]] = {}
        self._context_index: dict[str, list[str]] = {}
        self._working_memory: dict[str, Any] = {}
        self._stats = {
            "total_memories": 0,
            "total_queries": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

    async def store_memory(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.SHORT_TERM,
        importance: MemoryImportance = MemoryImportance.MEDIUM,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        context_id: str | None = None,
        session_id: str | None = None,
        expires_in_hours: int | None = None,
    ) -> MemoryBlock:
        embedding: list[float] | None = None
        if self.embedding_fn and memory_type in (MemoryType.SEMANTIC, MemoryType.EPISODIC):
            try:
                embedding = await asyncio.to_thread(self.embedding_fn, content)
            except Exception as exc:
                log.warning("embedding_generation_failed", error=str(exc))

        now = datetime.now(UTC)
        memory_id = str(uuid4())
        memory = MemoryBlock(
            memory_id=memory_id,
            content=content,
            memory_type=memory_type,
            embedding=embedding,
            importance=importance,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
            tags=tags or [],
            context_id=context_id,
            session_id=session_id,
            expires_at=now + timedelta(hours=expires_in_hours) if expires_in_hours else None,
        )

        self._memories[memory_id] = memory
        self._type_index[memory_type].append(memory_id)

        for tag in memory.tags:
            self._tag_index.setdefault(tag, []).append(memory_id)

        if context_id:
            self._context_index.setdefault(context_id, []).append(memory_id)

        if embedding:
            await self.vector_store.add_vectors(memory_id, embedding)

        self._stats["total_memories"] += 1
        return memory

    async def retrieve_memory(self, memory_id: str) -> MemoryBlock | None:
        memory = self._memories.get(memory_id)
        if memory:
            if memory.is_expired():
                await self.delete_memory(memory_id)
                return None
            memory.access_count += 1
            memory.last_accessed = datetime.now(UTC)
            self._stats["cache_hits"] += 1
        return memory

    async def search_memories(self, query: MemoryQuery) -> list[MemorySearchResult]:
        self._stats["total_queries"] += 1
        candidates: set[str] = set()

        if query.query_embedding:
            similar = await self.vector_store.search_similar(
                query.query_embedding, limit=query.limit * 2
            )
            for memory_id, _ in similar:
                if memory_id in self._memories:
                    candidates.add(memory_id)

        if len(candidates) < query.limit:
            query_tokens = self._tokens(query.query_text)
            for memory_id, memory in self._memories.items():
                if memory_id in candidates:
                    continue
                if query_tokens and self._tokens(memory.content) & query_tokens:
                    candidates.add(memory_id)
                elif query.query_text.lower() in memory.content.lower():
                    candidates.add(memory_id)

        results: list[MemorySearchResult] = []
        for memory_id in candidates:
            memory = self._memories.get(memory_id)
            if not memory or memory.is_expired():
                continue
            if query.memory_types and memory.memory_type not in query.memory_types:
                continue
            if query.tags and not any(t in memory.tags for t in query.tags):
                continue
            if query.min_importance and memory.importance.value < query.min_importance.value:
                continue

            relevance, breakdown = self._rank_memory(memory, query)
            if query.query_embedding and memory.embedding:
                sem = self.vector_store._cosine_similarity(query.query_embedding, memory.embedding)
                breakdown["semantic"] = round(sem * 100, 3)
                relevance = relevance * 0.35 + sem * 100 * 0.65

            results.append(
                MemorySearchResult(
                    memory=memory,
                    relevance_score=round(relevance, 3),
                    highlight=self._highlight(memory, query.query_text),
                    score_breakdown=breakdown,
                )
            )

        results.sort(key=lambda x: x.relevance_score, reverse=True)
        return results[: query.limit]

    async def contextual_recall(
        self,
        query_text: str,
        *,
        context_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        results = await self.search_memories(
            MemoryQuery(
                query_text=query_text,
                context_id=context_id,
                session_id=session_id,
                tags=tags,
                limit=limit,
            )
        )
        return [
            {
                "memory_id": result.memory.memory_id,
                "type": result.memory.memory_type.value,
                "content": result.memory.compact_summary(),
                "score": result.relevance_score,
                "tags": list(result.memory.tags),
                "breakdown": result.score_breakdown,
            }
            for result in results
        ]

    async def compress_context(
        self,
        query_text: str,
        *,
        context_id: str | None = None,
        limit: int = 6,
        max_chars: int = 900,
    ) -> str:
        recalled = await self.contextual_recall(query_text, context_id=context_id, limit=limit)
        lines: list[str] = []
        total = 0
        for item in recalled:
            line = f"- [{item['type']} score={item['score']:.1f}] {item['content']}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)

    async def smart_cleanup(self, *, max_memories: int = 5_000) -> MemoryConsolidationResult:
        result = await self.consolidate_memories()
        overflow = max(0, len(self._memories) - max_memories)
        if overflow <= 0:
            return result

        candidates = sorted(
            (
                memory
                for memory in self._memories.values()
                if memory.importance != MemoryImportance.CRITICAL
            ),
            key=lambda memory: (memory.get_importance_score(), memory.updated_at),
        )
        deleted = 0
        for memory in candidates[:overflow]:
            if await self.delete_memory(memory.memory_id):
                deleted += 1
        result.deleted_count += deleted
        return result

    async def get_working_memory(self, key: str) -> Any:
        return self._working_memory.get(key)

    async def set_working_memory(self, key: str, value: Any) -> None:
        self._working_memory[key] = value

    async def clear_working_memory(self) -> None:
        self._working_memory.clear()

    async def get_memories_by_type(
        self, memory_type: MemoryType, limit: int = 50
    ) -> list[MemoryBlock]:
        ids = self._type_index.get(memory_type, [])
        memories = []
        for mid in ids[-limit:]:
            m = self._memories.get(mid)
            if m and not m.is_expired():
                memories.append(m)
        return memories

    def _tokens(self, text: str) -> set[str]:
        return {match.group(0).lower() for match in _TOKEN_RE.finditer(text)}

    def _rank_memory(
        self, memory: MemoryBlock, query: MemoryQuery
    ) -> tuple[float, dict[str, float]]:
        query_tokens = self._tokens(query.query_text)
        memory_tokens = self._tokens(memory.content)
        lexical = 0.0
        if query_tokens and memory_tokens:
            lexical = len(query_tokens & memory_tokens) / max(len(query_tokens), 1)
        tag_score = 0.0
        if query.tags:
            tag_score = 1.0 if any(tag in memory.tags for tag in query.tags) else 0.0
        context_score = 1.0 if query.context_id and memory.context_id == query.context_id else 0.0
        session_score = 1.0 if query.session_id and memory.session_id == query.session_id else 0.0
        importance = memory.get_importance_score() / 100.0
        recency = self._recency_score(memory) if query.prefer_recent else 0.5
        access = min(memory.access_count / 20.0, 1.0)

        score = (
            lexical * 44.0
            + importance * 24.0
            + recency * 14.0
            + access * 6.0
            + tag_score * 7.0
            + context_score * 3.0
            + session_score * 2.0
        )
        return score, {
            "lexical": round(lexical * 100, 3),
            "importance": round(importance * 100, 3),
            "recency": round(recency * 100, 3),
            "access": round(access * 100, 3),
            "tag": round(tag_score * 100, 3),
            "context": round(context_score * 100, 3),
            "session": round(session_score * 100, 3),
        }

    def _recency_score(self, memory: MemoryBlock) -> float:
        age_hours = max(
            0.0,
            (datetime.now(UTC) - memory.updated_at).total_seconds() / 3600,
        )
        if age_hours <= 1:
            return 1.0
        if age_hours <= 24:
            return 0.75
        if age_hours <= 168:
            return 0.45
        return 0.20

    def _highlight(self, memory: MemoryBlock, query_text: str) -> str | None:
        tokens = self._tokens(query_text)
        if not tokens:
            return None
        words = memory.content.split()
        for idx, word in enumerate(words):
            if word.lower().strip(".,:;()[]{}") in tokens:
                start = max(0, idx - 6)
                end = min(len(words), idx + 10)
                return " ".join(words[start:end])
        return memory.compact_summary(160)

    async def get_memories_by_context(self, context_id: str, limit: int = 50) -> list[MemoryBlock]:
        ids = self._context_index.get(context_id, [])
        memories = []
        for mid in ids[-limit:]:
            m = self._memories.get(mid)
            if m and not m.is_expired():
                memories.append(m)
        return memories

    async def delete_memory(self, memory_id: str) -> bool:
        if memory_id not in self._memories:
            return False
        memory = self._memories[memory_id]
        ids = self._type_index.get(memory.memory_type, [])
        if memory_id in ids:
            ids.remove(memory_id)
        for tag in memory.tags:
            tag_ids = self._tag_index.get(tag, [])
            if memory_id in tag_ids:
                tag_ids.remove(memory_id)
        if memory.context_id:
            ctx_ids = self._context_index.get(memory.context_id, [])
            if memory_id in ctx_ids:
                ctx_ids.remove(memory_id)
        await self.vector_store.delete_vector(memory_id)
        del self._memories[memory_id]
        self._stats["total_memories"] -= 1
        return True

    async def consolidate_memories(self) -> MemoryConsolidationResult:
        consolidated = deleted = updated = 0
        errors: list[str] = []

        for memory_id, memory in list(self._memories.items()):
            try:
                if memory.should_consolidate():
                    stm_ids = self._type_index.get(MemoryType.SHORT_TERM, [])
                    if memory_id in stm_ids:
                        stm_ids.remove(memory_id)
                    memory.memory_type = MemoryType.LONG_TERM
                    memory.updated_at = datetime.now(UTC)
                    self._type_index.setdefault(MemoryType.LONG_TERM, []).append(memory_id)
                    consolidated += 1
                    updated += 1
                elif memory.is_expired():
                    await self.delete_memory(memory_id)
                    deleted += 1
            except Exception as exc:
                errors.append(f"Memory {memory_id}: {exc}")

        return MemoryConsolidationResult(
            consolidated_count=consolidated,
            deleted_count=deleted,
            updated_count=updated,
            errors=errors,
        )

    async def get_statistics(self) -> dict[str, Any]:
        counts = {t.value: len(self._type_index[t]) for t in MemoryType}
        queries = max(1, self._stats["total_queries"])
        return {
            "total_memories": self._stats["total_memories"],
            "by_type": counts,
            "total_queries": self._stats["total_queries"],
            "cache_hits": self._stats["cache_hits"],
            "cache_hit_rate": (self._stats["cache_hits"] / queries) * 100,
            "vector_store_size": await self.vector_store.get_memory_count(),
            "working_memory_size": len(self._working_memory),
            "unique_tags": len(self._tag_index),
            "unique_contexts": len(self._context_index),
        }

    async def get_context_summary(self, context_id: str, max_memories: int = 10) -> dict[str, Any]:
        memories = await self.get_memories_by_context(context_id, max_memories)
        if not memories:
            return {"context_id": context_id, "memory_count": 0, "summary": "No memories found"}
        total_importance = sum(m.get_importance_score() for m in memories)
        return {
            "context_id": context_id,
            "memory_count": len(memories),
            "avg_importance": total_importance / len(memories),
            "latest_memory": max(memories, key=lambda m: m.created_at).created_at.isoformat(),
            "most_accessed": max(memories, key=lambda m: m.access_count).content[:100],
        }


class SessionMemory:
    """Session-scoped memory facade."""

    def __init__(self, memory_manager: MemoryManager) -> None:
        self.memory_manager = memory_manager

    async def start_session(
        self,
        session_id: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        await self.memory_manager.store_memory(
            content=f"Session {session_id} started",
            memory_type=MemoryType.EPISODIC,
            importance=MemoryImportance.LOW,
            metadata={
                "session_id": session_id,
                "user_id": user_id,
                "event": "session_start",
                **(metadata or {}),
            },
            session_id=session_id,
        )
        return session_id

    async def end_session(self, session_id: str, summary: str | None = None) -> dict[str, Any]:
        memories = await self.memory_manager.get_memories_by_context(session_id)
        if summary is None:
            summary = f"Session contained {len(memories)} memories"
        await self.memory_manager.store_memory(
            content=summary,
            memory_type=MemoryType.EPISODIC,
            importance=MemoryImportance.MEDIUM,
            metadata={
                "session_id": session_id,
                "event": "session_end",
                "memory_count": len(memories),
            },
            session_id=session_id,
        )
        return {"session_id": session_id, "memory_count": len(memories), "summary": summary}

    async def add_interaction(
        self,
        session_id: str,
        interaction_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.memory_manager.store_memory(
            content=content,
            memory_type=MemoryType.EPISODIC,
            importance=MemoryImportance.MEDIUM,
            metadata={
                "session_id": session_id,
                "interaction_type": interaction_type,
                **(metadata or {}),
            },
            session_id=session_id,
        )
