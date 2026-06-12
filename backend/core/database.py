"""Async SQLAlchemy 2.0 engine + session factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

_settings = get_settings()
_database_url = str(_settings.database_url)


def _create_engine_options() -> dict[str, object]:
    options: dict[str, object] = {
        "echo": not _settings.is_production,
    }

    if _database_url.lower().startswith("sqlite"):
        return options

    options.update(
        pool_size=_settings.database_pool_size,
        max_overflow=_settings.database_pool_max_overflow,
        pool_timeout=_settings.database_pool_timeout,
        pool_pre_ping=True,
    )
    return options


engine = create_async_engine(_database_url, **_create_engine_options())

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency -- yields an async session and commits on exit."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for use outside of FastAPI (background tasks, etc.)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables. Called at startup when running without Alembic."""
    import backend.models.audit  # noqa: F401
    import backend.models.license  # noqa: F401
    import backend.models.session  # noqa: F401
    from backend.models.base import Base  # noqa: F401 -- import all models

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()
