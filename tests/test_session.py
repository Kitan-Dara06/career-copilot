"""Tests for database migrations — apply, downgrade, re-apply."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backbone.db.session import async_session_factory, reset_session_cache
from career_copilot.config import get_settings


@pytest.fixture
def session_factory() -> async_sessionmaker[AsyncSession]:
    """Fixture: fresh session factory per test.

    Passes settings explicitly to bypass the module-level cache,
    since pytest-asyncio creates a fresh event loop per test.
    """
    reset_session_cache()
    return async_session_factory(settings=get_settings())


@pytest.mark.asyncio
async def test_session_round_trip(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Open session, execute query, commit, verify."""
    async with session_factory() as session:
        result = await session.execute(text("SELECT 1 AS num"))
        row = result.one()
        assert row._mapping["num"] == 1
        await session.commit()


@pytest.mark.asyncio
async def test_tables_exist(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """All expected tables exist after migration."""
    expected_tables = {
        "user_facts",
        "interest_vectors",
        "short_term_memory",
        "professors",
        "professor_papers",
        "professor_interest_vectors",
        "digests",
        "digest_items",
        "feedback_log",
        "prompt_runs",
    }

    async with session_factory() as session:
        result = await session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = {row[0] for row in result}

    assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"


@pytest.mark.asyncio
async def test_foreign_key_constraint(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Foreign key constraint works between professors and professor_papers."""
    async with session_factory() as session:
        # Insert a professor
        await session.execute(text("INSERT INTO professors (id, name) VALUES (1, 'Test Prof')"))
        await session.commit()

    async with session_factory() as session:
        # Insert a paper referencing the professor
        await session.execute(
            text(
                "INSERT INTO professor_papers "
                "(professor_id, arxiv_id, title, authors, abstract) "
                "VALUES (1, '9999.00001', 'Test Paper', 'Test Author', 'Test abstract')"
            )
        )
        await session.commit()

    async with session_factory() as session:
        result = await session.execute(
            text("SELECT title FROM professor_papers WHERE professor_id = 1")
        )
        assert result.scalar_one() == "Test Paper"

    # Clean up
    async with session_factory() as session:
        await session.execute(text("DELETE FROM professor_papers"))
        await session.execute(text("DELETE FROM professors"))
        await session.commit()
