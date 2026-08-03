"""Async SQLAlchemy session factory and context manager."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from career_copilot.config import Settings

# Module-level cache — ONE engine per process, reused across all calls.
# Passing settings explicitly bypasses the cache (used by tests).
_engine = None
_factory: async_sessionmaker[AsyncSession] | None = None


def async_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Build (or return cached) async sessionmaker.

    The engine is created once and cached — critical because every
    agent method (watch_add, build_prof_brief_data, etc.) calls this.
    Without caching, each call creates a new engine + connection pool,
    leaking connections and causing inconsistent reads.

    Pass ``settings`` explicitly to bypass the cache (used by tests
    that run in fresh event loops per test case).
    """
    global _engine, _factory  # noqa: PLW0603

    if settings is not None:
        # Explicit settings — always create fresh (for tests)
        engine = create_async_engine(
            settings.database_url,
            echo=settings.database_echo,
            pool_size=5,
            max_overflow=10,
        )
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    if _factory is not None:
        return _factory

    from career_copilot.config import get_settings

    settings = get_settings()

    _engine = create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=5,
        max_overflow=10,
    )
    _factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _factory


def reset_session_cache() -> None:
    """Clear the cached engine (for tests that need fresh event loops)."""
    global _engine, _factory
    _engine = None
    _factory = None


@asynccontextmanager
async def get_session(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager yielding a session.

    Commits on success, rolls back on error.

    Args:
        session_factory: Optional override (defaults to a fresh factory).

    Yields:
        An AsyncSession ready for queries.

    Example:
        async with get_session() as session:
            result = await session.execute(text("SELECT 1"))
    """
    if session_factory is None:
        session_factory = async_session_factory()

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
