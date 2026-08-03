"""Short-term memory — PostgreSQL-backed with TTL.

Records expire after a configurable TTL and are purged by a background
task that runs every 5 minutes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backbone.db.session import async_session_factory

from .types import MemoryError, MemoryLayer, MemoryQuery, MemoryRecord


def _now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(UTC)


async def set(
    record: MemoryRecord,
    ttl: timedelta = timedelta(days=7),
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Write a record to short-term memory with a TTL.

    Args:
        record: The memory record to store.
        ttl: How long the record should live (default 7 days).
        session_factory: Optional session factory override.
    """
    if record.layer != MemoryLayer.SHORT_TERM:
        raise MemoryError(f"Expected SHORT_TERM layer, got {record.layer}")

    factory = session_factory or async_session_factory()
    expires_at = _now() + ttl

    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO short_term_memory (namespace, key, value, expires_at)
                VALUES (:namespace, :key, :value::jsonb, :expires_at)
                """
            ),
            {
                "namespace": record.namespace,
                "key": record.key,
                "value": json.dumps(record.value),
                "expires_at": expires_at,
            },
        )
        await session.commit()


async def get(
    query: MemoryQuery,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[MemoryRecord]:
    """Retrieve records from short-term memory, filtered by namespace and optional key.

    Args:
        query: The query parameters (namespace, optional key, optional TTL filter).
        session_factory: Optional session factory override.

    Returns:
        A list of matching MemoryRecords (not expired).
    """
    factory = session_factory or async_session_factory()

    async with factory() as session:
        if query.key:
            result = await session.execute(
                text(
                    """
                    SELECT namespace, key, value, expires_at
                    FROM short_term_memory
                    WHERE namespace = :namespace AND key = :key
                      AND expires_at > :now
                    """
                ),
                {
                    "namespace": query.namespace,
                    "key": query.key,
                    "now": _now(),
                },
            )
        else:
            result = await session.execute(
                text(
                    """
                    SELECT namespace, key, value, expires_at
                    FROM short_term_memory
                    WHERE namespace = :namespace
                      AND expires_at > :now
                    ORDER BY created_at DESC
                    """
                ),
                {
                    "namespace": query.namespace,
                    "now": _now(),
                },
            )

        records: list[MemoryRecord] = []
        for row in result:
            records.append(
                MemoryRecord(
                    namespace=row.namespace,
                    key=row.key,
                    value=row.value,
                    layer=MemoryLayer.SHORT_TERM,
                )
            )
        return records


async def purge_expired(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Delete all expired short-term memory records.

    Args:
        session_factory: Optional session factory override.

    Returns:
        The number of records deleted.
    """
    factory = session_factory or async_session_factory()
    async with factory() as session:
        result = await session.execute(
            text("DELETE FROM short_term_memory WHERE expires_at <= :now"),
            {"now": _now()},
        )
        await session.commit()
        count: Any = result.rowcount  # type: ignore[attr-defined]
        return count if count is not None else 0


async def schedule_purge_every_5min(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Background task that purges expired records every 5 minutes.

    Runs indefinitely; should be started as a background asyncio task.
    """
    while True:
        await asyncio.sleep(300)
        try:
            deleted = await purge_expired(session_factory=session_factory)
            if deleted:
                import structlog

                structlog.get_logger("memory.short_term").info("purged_expired", count=deleted)
        except Exception:
            import structlog

            structlog.get_logger("memory.short_term").exception("purge_failed")
