"""Vector tools — embed (Voyage 3) + search/upsert (Qdrant Cloud).

Voyage 3 produces 1024-dim embeddings. Qdrant stores and searches vectors.
No pgvector dependency — vectors live entirely in Qdrant.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext

# ── Data models ──


class EmbedInput(BaseModel):
    """Input for vector.embed."""

    texts: list[str]


class EmbedOutput(BaseModel):
    """Output for vector.embed — 1024-dim Voyage 3 vectors."""

    embeddings: list[list[float]]


class SearchInput(BaseModel):
    """Input for vector.search."""

    namespace: str
    query_embedding: list[float]
    k: int = 10


class ScoredRecord(BaseModel):
    """A scored result from vector search."""

    point_id: str
    score: float
    payload: dict[str, Any]


class SearchOutput(BaseModel):
    """Output for vector.search."""

    results: list[ScoredRecord]


class UpsertInput(BaseModel):
    """Input for vector.upsert."""

    namespace: str
    point_id: str
    embedding: list[float]
    payload: dict[str, Any]


class UpsertOutput(BaseModel):
    """Output for vector.upsert."""

    success: bool


# ── Helpers ──


def _collection_name(namespace: str) -> str:
    return f"career_copilot_{namespace.replace('/', '_')}"


_embed_cache: dict[str, list[float]] = {}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ── Tools ──


class EmbedTool(Tool[EmbedInput, EmbedOutput]):
    """Embed text using Voyage 3 (1024-dim)."""

    name = "vector.embed"
    description = "Embed text into 1024-dimensional vectors using Voyage 3."
    input_schema = EmbedInput
    output_schema = EmbedOutput
    cost_hint = CostHint.EXTERNAL_API_CALL
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: EmbedInput) -> EmbedOutput:
        settings = ctx.settings
        api_key = settings.voyage_api_key
        model = settings.voyage_model

        # Check cache first
        embeddings: list[list[float]] = []
        uncached: list[str] = []
        uncached_indices: list[int] = []
        for i, text in enumerate(input.texts):
            h = _hash(text)
            if h in _embed_cache:
                embeddings.append(_embed_cache[h])
            else:
                uncached.append(text)
                uncached_indices.append(i)

        if not uncached:
            return EmbedOutput(embeddings=embeddings)

        # Call Voyage API for uncached texts
        url = "https://api.voyageai.com/v1/embeddings"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "model": model,
            "input": uncached,
            "output_dimension": 1024,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()

        data = resp.json()
        new_embeddings = data["data"]

        # Cache new embeddings
        for text, emb in zip(uncached, new_embeddings, strict=True):
            _embed_cache[_hash(text)] = emb["embedding"]

        # Merge cached + new in original order
        result: list[list[float] | None] = [None] * len(input.texts)
        ei = 0
        for i, text in enumerate(input.texts):
            if i in uncached_indices:
                result[i] = new_embeddings[ei]["embedding"]
                ei += 1
            else:
                result[i] = _embed_cache[_hash(text)]

        return EmbedOutput(embeddings=result)  # type: ignore[arg-type]


class SearchTool(Tool[SearchInput, SearchOutput]):
    """Vector similarity search via Qdrant."""

    name = "vector.search"
    description = "Search for nearest neighbors by vector similarity in a Qdrant namespace."
    input_schema = SearchInput
    output_schema = SearchOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: SearchInput) -> SearchOutput:
        settings = ctx.settings
        client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        collection = _collection_name(input.namespace)

        # Ensure collection exists
        try:
            client.get_collection(collection)
        except Exception:
            client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
            )

        results = client.query_points(
            collection_name=collection,
            query=input.query_embedding,
            limit=input.k,
        ).points

        return SearchOutput(
            results=[
                ScoredRecord(
                    point_id=str(r.id),
                    score=r.score,
                    payload=r.payload or {},
                )
                for r in results
            ]
        )


class UpsertTool(Tool[UpsertInput, UpsertOutput]):
    """Upsert a point into a Qdrant namespace."""

    name = "vector.upsert"
    description = "Insert or update a vector point in a Qdrant namespace."
    input_schema = UpsertInput
    output_schema = UpsertOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: UpsertInput) -> UpsertOutput:
        settings = ctx.settings
        client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        collection = _collection_name(input.namespace)

        try:
            client.get_collection(collection)
        except Exception:
            client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
            )

        client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=input.point_id,
                    vector=input.embedding,
                    payload=input.payload,
                ),
            ],
        )

        return UpsertOutput(success=True)


# Auto-register
from backbone.tools.registry import register

register(EmbedTool(), agent="paper_tracker")
register(SearchTool(), agent="paper_tracker")
register(UpsertTool(), agent="paper_tracker")
