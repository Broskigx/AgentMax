"""Long-term memory -- JSON file persistence with optional ChromaDB vector store."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class LongTermMemory:
    """
    Long-term memory that persists to a JSON file.

    When ChromaDB is available, also enables semantic vector search.
    Otherwise, falls back to keyword search over the JSON store.

    Collections:
      - tasks: Completed task workflows
      - apps: App-specific UI knowledge
      - errors: Known error patterns and recoveries
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._client: Any = None
        self._collection: Any = None
        self._embedding_fn: Any = None

        # JSON persistence
        self._lock = threading.Lock()
        self._db_path = Path(getattr(config, "long_term_db_path", "./data/long_term.json"))
        self._data: dict[str, list[dict]] = {
            "tasks": [],
            "apps": [],
            "errors": [],
        }
        self._chroma_available = False

    async def start(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        # Load existing JSON data
        self._load_json()

        # Try ChromaDB for vector search
        try:
            import chromadb
            from chromadb.config import Settings

            persist_dir = str(getattr(self._config, "long_term_persist_dir", "./data/chroma"))
            self._client = chromadb.PersistentClient(
                path=persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )

            try:
                from chromadb.utils import embedding_functions

                model = getattr(self._config, "embedding_model", "all-MiniLM-L6-v2")
                self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=model
                )
            except Exception:
                self._embedding_fn = None

            self._collection = self._client.get_or_create_collection(
                name="AGENTMAX_tasks",
                embedding_function=self._embedding_fn,
                metadata={"hnsw:space": "cosine"},
            )
            self._chroma_available = True
            log.info(
                "ltm.chromadb_ready",
                count=self._collection.count(),
                persist_dir=persist_dir,
            )
        except ImportError:
            log.info("ltm.chromadb_not_available -- using JSON storage")
        except Exception as exc:
            log.warning("ltm.chromadb_init_error", error=str(exc))

    def _load_json(self) -> None:
        """Load persisted data from JSON file."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            if self._db_path.exists():
                raw = self._db_path.read_text(encoding="utf-8")
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    for key in self._data:
                        self._data[key] = loaded.get(key, [])
                log.info(
                    "ltm.json_loaded",
                    path=str(self._db_path),
                    entries=sum(len(v) for v in self._data.values()),
                )
        except Exception as exc:
            log.warning("ltm.json_load_error", error=str(exc))

    def _save_json(self) -> None:
        """Persist data to JSON file."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                self._db_path.write_text(
                    json.dumps(self._data, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
        except Exception as exc:
            log.warning("ltm.json_save_error", error=str(exc))

    async def stop(self) -> None:
        self._save_json()
        log.info("ltm.stopped", path=str(self._db_path))

    async def store(self, document: str, metadata: dict, doc_id: str) -> None:
        """Store a document in long-term memory."""
        collection = metadata.get("collection", "tasks")
        entry = {
            "id": doc_id,
            "document": document,
            "metadata": metadata,
            "timestamp": time.time(),
        }

        # Persist to JSON
        with self._lock:
            if collection in self._data:
                # Replace existing or append
                for i, existing in enumerate(self._data[collection]):
                    if existing.get("id") == doc_id:
                        self._data[collection][i] = entry
                        break
                else:
                    self._data[collection].append(entry)
                # Keep max 1000 entries per collection
                if len(self._data[collection]) > 1000:
                    self._data[collection] = self._data[collection][-1000:]
        self._save_json()

        # Also store in ChromaDB if available
        if self._collection:
            await asyncio.to_thread(
                self._collection.upsert,
                documents=[document],
                metadatas=[{**metadata, "db_id": doc_id}],
                ids=[doc_id],
            )

    async def search(self, query: str, n_results: int = 5, collection: str = "tasks") -> list[dict]:
        """
        Search long-term memory.

        Uses ChromaDB for semantic search when available.
        Falls back to simple keyword matching over the JSON store.
        """
        # Try ChromaDB first
        if self._collection:
            try:
                results = await asyncio.to_thread(
                    self._collection.query,
                    query_texts=[query],
                    n_results=min(n_results, self._collection.count()),
                )
                out = []
                for i, doc in enumerate(results["documents"][0]):
                    try:
                        data = json.loads(doc)
                        data["score"] = 1 - results["distances"][0][i]
                        out.append(data)
                    except json.JSONDecodeError:
                        out.append({"text": doc, "score": 1 - results["distances"][0][i]})
                return out
            except Exception as exc:
                log.warning("ltm.chroma_search_error", error=str(exc))
                # Fall through to keyword search

        # Keyword fallback over JSON store
        query_lower = query.lower()
        terms = query_lower.split()
        results = []

        with self._lock:
            entries = self._data.get(collection, [])

        for entry in entries:
            doc_text = (
                entry.get("document", "") + " " + json.dumps(entry.get("metadata", {}))
            ).lower()
            match_count = sum(1 for term in terms if term in doc_text)
            if match_count > 0:
                score = match_count / max(len(terms), 1)
                results.append(
                    {
                        "id": entry["id"],
                        "text": entry.get("document", "")[:200],
                        "metadata": entry.get("metadata", {}),
                        "score": round(score, 3),
                        "timestamp": entry.get("timestamp", 0),
                    }
                )

        # Sort by score, then by recency
        results.sort(key=lambda r: (r["score"], r.get("timestamp", 0)), reverse=True)
        return results[:n_results]

    async def delete(self, doc_id: str, collection: str = "tasks") -> None:
        """Delete a document by ID."""
        with self._lock:
            if collection in self._data:
                self._data[collection] = [
                    e for e in self._data[collection] if e.get("id") != doc_id
                ]
        self._save_json()

        if self._collection:
            await asyncio.to_thread(self._collection.delete, ids=[doc_id])

    async def count(self, collection: str = "tasks") -> int:
        if self._collection:
            try:
                return await asyncio.to_thread(self._collection.count)
            except Exception:
                pass
        with self._lock:
            return len(self._data.get(collection, []))

    def get_stats(self) -> dict[str, int]:
        """Get storage statistics."""
        with self._lock:
            return {k: len(v) for k, v in self._data.items()}
