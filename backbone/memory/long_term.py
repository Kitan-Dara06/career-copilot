"""Long-term memory — PostgreSQL for versioning metadata + Qdrant for vector storage.

Records in long-term memory are:
- **Versioned**: every write creates a new version. Rollback is supported.
- **Vector-searchable**: via Qdrant Cloud (not pgvector).
- **Namespaced**: access is gated by the namespace module.

Qdrant collections map 1:1 to namespaces. Each point in Qdrant stores the
record value as payload and the embedding as the vector.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    PointStruct,
    ScoredPoint,
    VectorParams,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backbone.db.session import async_session_factory
from career_copilot.config import get_settings

from .types import (
    MemoryLayer,
    MemoryQuery,
    MemoryRecord,
)

logger = structlog.get_logger("memory.long_term")


def _now() -> datetime:
    return datetime.now(UTC)


def _get_qdrant_client() -> QdrantClient:
    """Return a Qdrant client configured from settings."""
    settings = get_settings()
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
    )


def _collection_name(namespace: str) -> str:
    """Derive a Qdrant collection name from a namespace."""
    return f"career_copilot_{namespace.replace('/', '_')}"


async def _ensure_collection(namespace: str) -> None:
    """Create the Qdrant collection if it doesn't exist."""
    client = _get_qdrant_client()
    collection = _collection_name(namespace)
    try:
        client.get_collection(collection)
    except Exception:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
        )
        logger.info("created_qdrant_collection", collection=collection)


async def set(
    record: MemoryRecord,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Write a versioned record to long-term memory.

    The value is stored in Qdrant as a point payload.
    The metadata (version history) is stored in PostgreSQL.
    A ``qdrant_id`` field links the two.

    Args:
        record: The memory record to store.
        session_factory: Optional session factory override.

    Returns:
        The new version number.
    """
    if record.layer != MemoryLayer.LONG_TERM:
        raise ValueError(f"Expected LONG_TERM layer, got {record.layer}")

    factory = session_factory or async_session_factory()
    client = _get_qdrant_client()
    collection = _collection_name(record.namespace)
    await _ensure_collection(record.namespace)

    # Determine next version
    async with factory() as session:
        result = await session.execute(
            text(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM long_term_versions
                WHERE namespace = :namespace AND key = :key
                """,
            ),
            {"namespace": record.namespace, "key": record.key},
        )
        new_version = result.scalar_one()

        # Upsert the version metadata
        await session.execute(
            text(
                """
                INSERT INTO long_term_versions (namespace, key, version, qdrant_id, created_at)
                VALUES (:namespace, :key, :version, :qdrant_id, :created_at)
                """,
            ),
            {
                "namespace": record.namespace,
                "key": record.key,
                "version": new_version,
                "qdrant_id": f"{record.namespace}:{record.key}:v{new_version}",
                "created_at": _now(),
            },
        )
        await session.commit()

    # Upsert into Qdrant
    point_id = f"{record.namespace}:{record.key}:v{new_version}"
    payload: dict[str, str] = {
        "namespace": record.namespace,
        "key": record.key,
        "version": new_version,
        "value": json.dumps(record.value),
        "metadata": json.dumps(record.metadata),
    }

    client.upsert(
        collection_name=collection,
        points=[
            PointStruct(
                id=point_id,
                vector=record.embedding or [0.0] * 1024,
                payload=payload,
            ),
        ],
    )

    logger.info(
        "long_term_set",
        namespace=record.namespace,
        key=record.key,
        version=new_version,
    )
    return new_version  # type: ignore[no-any-return]


async def get(
    query: MemoryQuery,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[MemoryRecord]:
    """Retrieve records from long-term memory.

    If ``query.embedding`` is provided, performs vector similarity search
    via Qdrant. Otherwise, looks up by exact key.

    Args:
        query: The query parameters.
        session_factory: Optional session factory override.

    Returns:
        A list of matching MemoryRecords.
    """
    client = _get_qdrant_client()
    collection = _collection_name(query.namespace)
    await _ensure_collection(query.namespace)

    if query.embedding:
        # Vector search via Qdrant
        results: list[ScoredPoint] = client.query_points(
            collection_name=collection,
            query=query.embedding,
            limit=query.k,
        ).points
        return [
            MemoryRecord(
                namespace=(
                    result.payload.get("namespace", query.namespace)
                    if result.payload
                    else query.namespace
                ),
                key=result.payload.get("key", "") if result.payload else "",
                value=json.loads(result.payload.get("value", "null") if result.payload else "null"),
                layer=MemoryLayer.LONG_TERM,
                embedding=result.vector,  # type: ignore[arg-type]
                metadata=json.loads(
                    result.payload.get("metadata", "{}") if result.payload else "{}"
                ),
            )
            for result in results
        ]
    elif query.key:
        # Exact key lookup — fetch latest version from PostgreSQL
        factory = session_factory or async_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT qdrant_id
                    FROM long_term_versions
                    WHERE namespace = :namespace AND key = :key
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                ),
                {"namespace": query.namespace, "key": query.key},
            )
            row = result.one_or_none()
            if row is None:
                return []

            # Fetch from Qdrant by point ID
            points = client.retrieve(
                collection_name=collection,
                ids=[str(row.qdrant_id)],
            )
            if not points:
                return []

            pt = points[0]
            payload = pt.payload or {}
            return [
                MemoryRecord(
                    namespace=payload.get("namespace", query.namespace),
                    key=payload.get("key", query.key),
                    value=json.loads(payload.get("value", "null")),
                    layer=MemoryLayer.LONG_TERM,
                    metadata=json.loads(payload.get("metadata", "{}")),
                )
            ]
    else:
        return []


async def rollback(
    namespace: str,
    key: str,
    to_version: int,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Revert a record to a prior version.

    The rolled-back version is returned as the current "latest" in lookups.

    Args:
        namespace: The namespace.
        key: The record key.
        to_version: The version to roll back to.
        session_factory: Optional session factory override.
    """
    factory = session_factory or async_session_factory()
    async with factory() as session:
        # Mark all versions after to_version as rolled_back
        await session.execute(
            text(
                """
                UPDATE long_term_versions
                SET is_active = false
                WHERE namespace = :namespace AND key = :key AND version > :to_version
                """,
            ),
            {"namespace": namespace, "key": key, "to_version": to_version},
        )
        # Mark the target version as active
        await session.execute(
            text(
                """
                UPDATE long_term_versions
                SET is_active = true
                WHERE namespace = :namespace AND key = :key AND version = :to_version
                """,
            ),
            {"namespace": namespace, "key": key, "to_version": to_version},
        )
        await session.commit()

    logger.info("long_term_rollback", namespace=namespace, key=key, to_version=to_version)


async def history(
    namespace: str,
    key: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[dict[str, Any]]:
    """Return all versions of a record.

    Args:
        namespace: The namespace.
        key: The record key.
        session_factory: Optional session factory override.

    Returns:
        List of version metadata dicts (version, qdrant_id, created_at, is_active).
    """
    factory = session_factory or async_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                """
                SELECT version, qdrant_id, created_at, is_active
                FROM long_term_versions
                WHERE namespace = :namespace AND key = :key
                ORDER BY version DESC
                """,
            ),
            {"namespace": namespace, "key": key},
        )
        return [
            {
                "version": row.version,
                "qdrant_id": row.qdrant_id,
                "created_at": row.created_at,
                "is_active": row.is_active,
            }
            for row in result
        ]
