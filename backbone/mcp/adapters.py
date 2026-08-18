"""Adapters that expose Career Copilot data as MCP tool results.

Each adapter reads canonical data and returns a plain JSON-serializable
structure. Adapters do not perform writes and do not leak secrets.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree

import httpx
import yaml

from career_copilot.config.paths import DATA_DIR

# arXiv Atom namespace for paper search parsing.
_ARXIV_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_API = "https://export.arxiv.org/api/query"


def _load_yaml(name: str) -> dict[str, Any]:
    """Load a YAML file from the data directory, returning {} if missing."""
    path = DATA_DIR / name
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_profile() -> dict[str, Any]:
    """Return the canonical user profile and skill clusters.

    Sources:
        - data/user_profile.yaml  (research interests, keywords, preferences)
        - data/user_skills.yaml   (14 skill clusters with weights)
    """
    profile = _load_yaml("user_profile.yaml")
    skills_raw = _load_yaml("user_skills.yaml")
    skills = skills_raw.get("skills", {}) or {}

    return {
        "research_interests": (profile.get("research_interests") or "").strip(),
        "keywords": profile.get("keywords") or [],
        "arxiv_categories": profile.get("arxiv_categories") or [],
        "preferences": profile.get("preferences") or {},
        "skill_clusters": [
            {
                "name": name,
                "skills": body.get("skills") or [],
                "weight": body.get("weight", 1.0),
            }
            for name, body in skills.items()
        ],
    }


async def search_papers(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search arXiv by keyword, newest first.

    Returns a compact list of papers with id, title, authors, abstract
    excerpt, publish date, and URL. Used by ``career.papers.search``.
    """
    url = (
        f"{_ARXIV_API}?search_query=all:{quote(query)}"
        f"&start=0&max_results={max(1, min(int(limit), 20))}"
        f"&sortBy=submittedDate&sortOrder=descending"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    root = ElementTree.fromstring(resp.text)
    papers: list[dict[str, Any]] = []
    for entry in root.findall(f"{_ARXIV_NS}entry"):
        arxiv_id = entry.find(f"{_ARXIV_NS}id").text.rsplit("/abs/", 1)[-1]
        title = entry.find(f"{_ARXIV_NS}title").text.strip().replace("\n", " ")
        summary = entry.find(f"{_ARXIV_NS}summary").text.strip().replace("\n", " ")
        authors = [
            a.find(f"{_ARXIV_NS}name").text
            for a in entry.findall(f"{_ARXIV_NS}author")
            if a.find(f"{_ARXIV_NS}name") is not None
        ]
        papers.append(
            {
                "arxiv_id": arxiv_id,
                "title": title,
                "authors": authors[:3],
                "abstract": summary[:500],
                "published": entry.find(f"{_ARXIV_NS}published").text,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
            }
        )
    return papers


async def search_professors(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search the professor watchlist by name or affiliation."""
    from sqlalchemy import text

    from backbone.db.session import async_session_factory

    factory = async_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT name, affiliation, homepage_url, added_at"
                " FROM professors"
                " WHERE name ILIKE :q OR affiliation ILIKE :q"
                " ORDER BY added_at DESC"
                " LIMIT :limit"
            ),
            {"q": f"%{query}%", "limit": max(1, min(int(limit), 50))},
        )
        rows = result.all()
    return [
        {
            "name": r.name,
            "affiliation": r.affiliation,
            "homepage_url": r.homepage_url,
            "added_at": r.added_at.isoformat() if r.added_at else None,
        }
        for r in rows
    ]


async def search_jobs(
    query: str | None = None,
    region: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search discovered job openings by keyword and/or region."""
    from sqlalchemy import text

    from backbone.db.session import async_session_factory

    clauses: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 50))}
    if query:
        clauses.append("(title ILIKE :q OR organization ILIKE :q OR description ILIKE :q)")
        params["q"] = f"%{query}%"
    if region:
        clauses.append("region = :region")
        params["region"] = region
    where = " AND ".join(clauses) if clauses else "TRUE"

    factory = async_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT title, organization, region, location, remote_ok,"
                " application_url, posted_at"
                f" FROM job_hunter_openings WHERE {where}"
                " ORDER BY posted_at DESC NULLS LAST"
                " LIMIT :limit"
            ),
            params,
        )
        rows = result.all()
    return [
        {
            "title": r.title,
            "organization": r.organization,
            "region": r.region,
            "location": r.location,
            "remote_ok": r.remote_ok,
            "application_url": r.application_url,
            "posted_at": r.posted_at.isoformat() if r.posted_at else None,
        }
        for r in rows
    ]
