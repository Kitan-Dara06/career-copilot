"""Adapters that expose Career Copilot data as MCP tool results.

Each adapter reads canonical data and returns a plain JSON-serializable
structure. Adapters do not perform writes and do not leak secrets.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx
import yaml

from career_copilot.config.paths import DATA_DIR

# arXiv Atom namespace for paper search parsing.
_ARXIV_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_API = "https://export.arxiv.org/api/query"

# Words that add noise when used as standalone ILIKE tokens in DB searches.
_STOPWORDS = {
    "the", "for", "and", "with", "doing", "find", "search", "any", "some",
    "recent", "about", "that", "this", "from", "have", "has", "are", "were",
    "what", "where", "who", "how", "can", "could", "would", "should", "will",
    "into", "their", "them", "there", "jobs", "job", "professor", "professors",
    "papers", "paper", "list", "show", "get", "me", "my", "at", "in", "on",
}


def _search_tokens(query: str) -> list[str]:
    """Split a natural-language query into meaningful match tokens.

    "professors at McGill doing retrieval" -> ["McGill", "retrieval"].
    This lets DB ILIKE searches match ANY token instead of requiring the
    whole phrase, which would never match a stored affiliation string.
    """
    out: list[str] = []
    for t in query.split():
        t = t.strip(",.!?;:'\"()[]")
        if len(t) > 2 and t.lower() not in _STOPWORDS:
            out.append(t)
    return out[:8]


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
    """Search arXiv by keyword, most relevant first.

    Note on encoding: arXiv's API requires ``+`` (not ``%20``) for spaces in
    ``search_query`` — with ``%20`` it silently treats the terms as OR and
    returns noise. ``sortBy=relevance`` then ranks multi-term matches first.

    Returns a compact list of papers with id, title, authors, abstract
    excerpt, publish date, and URL. Used by ``career.papers.search``.
    """
    url = (
        f"{_ARXIV_API}?search_query=all:{quote_plus(query)}"
        f"&start=0&max_results={max(1, min(int(limit), 20))}"
        f"&sortBy=relevance&sortOrder=descending"
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
    """Search the professor watchlist by name or affiliation.

    The query is tokenized so natural phrases like "McGill doing retrieval"
    match any token (McGill OR retrieval) against name/affiliation.
    """
    from sqlalchemy import text

    from backbone.db.session import async_session_factory

    tokens = _search_tokens(query)
    if not tokens:
        return []
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 50))}
    for i, tok in enumerate(tokens):
        params[f"q{i}"] = f"%{tok}%"
        clauses.append(f"(name ILIKE :q{i} OR affiliation ILIKE :q{i})")
    where = " OR ".join(clauses)

    factory = async_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT name, affiliation, homepage_url, added_at"
                " FROM professors"
                f" WHERE {where}"
                " ORDER BY added_at DESC"
                " LIMIT :limit"
            ),
            params,
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
    """Search discovered job openings by keyword and/or region.

    The query is tokenized so natural phrases like "ML engineer jobs in
    nigeria" match any token against title, organization, or description.
    """
    from sqlalchemy import text

    from backbone.db.session import async_session_factory

    clauses: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 50))}
    tokens = _search_tokens(query or "")
    if tokens:
        tok_clauses: list[str] = []
        for i, tok in enumerate(tokens):
            params[f"q{i}"] = f"%{tok}%"
            tok_clauses.append(
                f"(title ILIKE :q{i} OR organization ILIKE :q{i} OR description ILIKE :q{i})"
            )
        clauses.append("(" + " OR ".join(tok_clauses) + ")")
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
