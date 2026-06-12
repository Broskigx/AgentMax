"""
Backend test fixtures.

Uses SQLite (aiosqlite) in-memory so tests run without a real PostgreSQL server.
Redis is replaced with fakeredis.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ── Force test environment before any backend import ─────────────────────────
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ED25519_PRIVATE_KEY_HEX", "a" * 64)
os.environ.setdefault("AES_MASTER_KEY_HEX", "b" * 64)
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-for-testing-only-32!")


# ── Shared admin secret for tests ────────────────────────────────────────────
ADMIN_SECRET = os.environ["ADMIN_SECRET"]


# ── App fixture (one per module to save setup time) ───────────────────────────
@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(scope="module")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client wired to the FastAPI app with mocked DB and Redis."""

    # Patch DB and Redis before importing the app
    mock_db_session = AsyncMock()
    mock_db_session.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_db_session.__aexit__ = AsyncMock(return_value=False)
    mock_db_session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    mock_db_session.commit = AsyncMock()
    mock_db_session.flush = AsyncMock()
    mock_db_session.add = MagicMock()

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.pipeline = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=AsyncMock(execute=AsyncMock(return_value=[1, True]))),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    with (
        patch("backend.core.database.init_db", new_callable=AsyncMock),
        patch("backend.core.database.close_db", new_callable=AsyncMock),
        patch("backend.core.redis_pool.get_redis", return_value=mock_redis),
        patch("backend.core.redis_pool.close_redis", new_callable=AsyncMock),
        patch("backend.core.database.get_db", return_value=mock_db_session),
        patch("backend.core.database.AsyncSessionLocal", return_value=mock_db_session),
    ):
        # Override the get_db dependency
        from backend.core.database import get_db
        from backend.main import app

        async def _override_get_db():
            yield mock_db_session

        app.dependency_overrides[get_db] = _override_get_db

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac

        app.dependency_overrides.clear()
