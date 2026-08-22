"""Sync interest facts from ``data/user_profile.yaml`` into ``user_facts``.

The profile + skills YAMLs are gitignored (personal data) and shipped to the
containers as base64 env vars (USER_PROFILE_B64 / USER_SKILLS_B64), decoded
by the entrypoints into ``data/`` at start. This module then makes the
interest facts the paper tracker/digest/discovery agents actually read
(``research_interests_essay``, ``research_keywords``) match the canonical
profile instead of the hardcoded fallback string.

Idempotent: upserts on conflict, safe to run on every boot.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text


async def sync_user_facts() -> dict[str, int]:
    """Upsert profile-derived facts; returns {fact_key: 1} for written rows."""
    from backbone.db.session import async_session_factory
    from backbone.mcp.adapters import load_profile

    profile = load_profile()
    research = (profile.get("research_interests") or "").strip()
    keywords = profile.get("keywords") or []
    if not research and not keywords:
        return {}

    writes: dict[str, dict[str, object]] = {}
    if research:
        writes["research_interests_essay"] = {"text": research}
    if keywords:
        writes["research_keywords"] = {"keywords": list(keywords)}

    factory = async_session_factory()
    written: dict[str, int] = {}
    async with factory() as session:
        for key, value in writes.items():
            await session.execute(
                text(
                    "INSERT INTO user_facts (key, value)"
                    " VALUES (:key, CAST(:value AS jsonb))"
                    " ON CONFLICT (key)"
                    " DO UPDATE SET value = CAST(:value AS jsonb)"
                ),
                {"key": key, "value": json.dumps(value)},
            )
            written[key] = 1
        await session.commit()
    return written


def main() -> None:
    written = asyncio.run(sync_user_facts())
    print(f"[seed_user_facts] synced {len(written)} fact(s): {sorted(written)}")


if __name__ == "__main__":
    main()