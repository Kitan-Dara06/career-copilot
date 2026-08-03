"""Seed script — embed the research interests essay and store the interest vector.

Usage:
    uv run python -m scripts.seed_interest_vector

This calls Voyage 3 to embed the essay, stores the 1024-dim vector in:
- PostgreSQL: interest_vectors table (id=1, source='seed', is_active=true)
- Qdrant Cloud: paper_tracker/papers_summarized collection
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import httpx
import structlog
import yaml

from career_copilot.config import get_settings

logger = structlog.get_logger("seed")


def load_profile(path: str = "data/user_profile.yaml") -> dict:
    """Load the user profile YAML."""
    full_path = Path(__file__).resolve().parent.parent / path
    with open(full_path) as f:
        return yaml.safe_load(f)


async def embed_voyage(text: str, settings) -> list[float]:
    """Embed a single text via Voyage 3 API."""
    api_key = settings.voyage_api_key
    if not api_key:
        print("❌ VOYAGE_API_KEY not set in .env")
        raise SystemExit(1)

    url = "https://api.voyageai.com/v1/embeddings"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"model": "voyage-3", "input": text, "output_dimension": 1024}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()

    data = resp.json()
    return data["data"][0]["embedding"]


async def store_postgres(settings, _embedding: list[float]) -> None:
    """Store interest vector in PostgreSQL."""
    from sqlalchemy import text

    from backbone.db.session import async_session_factory

    factory = async_session_factory(settings)
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO interest_vectors (qdrant_id, source, is_active) "
                "VALUES (:qdrant_id, :source, true) "
                "ON CONFLICT DO NOTHING"
            ),
            {"qdrant_id": "interest_vector_seed_v1", "source": "seed"},
        )

        # Also store user facts with the essay and keywords
        profile = load_profile()
        await session.execute(
            text(
                "INSERT INTO user_facts (key, value) VALUES (:key, :value)"
                " ON CONFLICT (key) DO UPDATE SET value = :value"
            ),
            {
                "key": "research_interests_essay",
                "value": json.dumps({"text": profile["research_interests"]}),
            },
        )
        await session.execute(
            text(
                "INSERT INTO user_facts (key, value) VALUES (:key, :value)"
                " ON CONFLICT (key) DO UPDATE SET value = :value"
            ),
            {
                "key": "research_keywords",
                "value": json.dumps({"keywords": profile["keywords"]}),
            },
        )
        await session.execute(
            text(
                "INSERT INTO user_facts (key, value) VALUES (:key, :value)"
                " ON CONFLICT (key) DO UPDATE SET value = :value"
            ),
            {
                "key": "arxiv_categories",
                "value": json.dumps({"categories": profile["arxiv_categories"]}),
            },
        )
        await session.commit()

    print("✅ Interest vector stored in PostgreSQL (qdrant_id=interest_vector_seed_v1)")


async def store_qdrant(settings, embedding: list[float]) -> None:
    """Store the interest vector in Qdrant."""
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Distance, PointStruct, VectorParams

    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    collection = "career_copilot_user_profile"

    # Create collection if needed
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
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "type": "interest_vector",
                    "source": "seed",
                    "research_keywords": load_profile().get("keywords", []),
                },
            ),
        ],
    )
    print(f"✅ Interest vector stored in Qdrant (collection={collection})")


async def main() -> None:
    settings = get_settings()
    profile = load_profile()
    essay = profile["research_interests"].strip()

    if not essay:
        print("❌ No research_interests found in data/user_profile.yaml")
        return

    print(f"📝 Research essay: {len(essay)} characters, ~{len(essay.split())} words")
    print(f"🔑 Keywords: {', '.join(profile['keywords'][:5])}...")

    # Embed
    embedding = await embed_voyage(essay, settings)
    print(f"🧬 Voyage 3 embedding: {len(embedding)} dimensions")

    # Store in Postgres
    await store_postgres(settings, embedding)

    # Store in Qdrant
    try:
        await store_qdrant(settings, embedding)
    except Exception as exc:
        print(f"⚠️  Qdrant store failed (non-fatal): {exc}")

    print()
    print("✅ Interest vector seeded successfully!")
    print("   Now run: python -m career_copilot serve --polling")
    print("   Then try: /discover")


if __name__ == "__main__":
    asyncio.run(main())
