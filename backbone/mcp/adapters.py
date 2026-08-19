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


def _tsvector_expr(columns: str) -> str:
    """Postgres tsvector over the given columns, null-safe.

    ``plainto_tsquery`` (used in the WHERE clauses) handles stopword
    removal and stemming natively, so no hand-rolled stopword list is
    needed. ``ts_rank`` orders by match quality.
    """
    parts = [f"coalesce({c}, '')" for c in columns]
    return f"to_tsvector('english', {' || '.join(parts)})"


# Query keywords → CSRankings parent area, for scoping professor discovery.
_AREA_KEYWORDS: dict[str, str] = {
    "retrieval": "inforet",
    "information retrieval": "inforet",
    "web search": "inforet",
    "nlp": "nlp",
    "natural language": "nlp",
    "language processing": "nlp",
    "machine learning": "mlmining",
    "deep learning": "mlmining",
    "artificial intelligence": "ai",
    "computer vision": "vision",
    "vision": "vision",
}

_DEFAULT_DISCOVER_AREAS: set[str] = {"nlp", "inforet", "mlmining", "ai"}


def should_discover(query: str) -> bool:
    """True if the query targets NEW professors (not just the watchlist).

    Heuristic: the query contains a capitalized token (likely an institution
    or proper noun) or an area keyword. "who am I watching" → False;
    "professors at McGill doing retrieval" → True.
    """
    if not query or not query.strip():
        return False
    if any(c.isupper() for c in query):
        return True
    q = query.lower()
    return any(kw in q for kw in _AREA_KEYWORDS)


async def discover_professors(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Find professors from CSRankings matching an institution and/or area.

    Query tokens are matched against CSRankings' institution list (e.g.
    "McGill" → "McGill University") and area keywords ("retrieval" →
    inforet). Results are ranked by adjusted publication count. The upstream
    CSVs are downloaded once per process and cached.
    """
    from backbone.tools.csrankings import (
        CONFERENCE_TO_PARENT,
        _load_author_info,
        _load_institutions,
        _load_prof_index,
    )

    q_lower = query.lower()
    tokens = [
        t.strip(",.!?;:'\"()[]")
        for t in query.split()
        if len(t.strip(",.!?;:'\"()[]")) > 2
    ]

    areas: set[str] = set()
    for kw, area in _AREA_KEYWORDS.items():
        if kw in q_lower:
            areas.add(area)
    if not areas:
        areas = set(_DEFAULT_DISCOVER_AREAS)

    institutions = await _load_institutions()
    inst_names = sorted(
        (n for n in institutions if any(t.lower() in n.lower() for t in tokens)),
        key=len,
        reverse=True,
    )

    author_info = await _load_author_info()
    prof_index = await _load_prof_index()

    # Adjusted publication count per author, limited to hinted areas.
    adjusted: dict[str, float] = {}
    for row_area, name, adj in author_info["rows"]:
        parent = CONFERENCE_TO_PARENT.get(row_area)
        if parent is None or parent not in areas:
            continue
        adjusted[name] = adjusted.get(name, 0.0) + adj

    matches: list[dict[str, Any]] = []
    for prof_row in prof_index:
        name = prof_row.get("name", "")
        affiliation = prof_row.get("affiliation", "") or ""
        if not name or not affiliation:
            continue
        if inst_names and not any(inst in affiliation for inst in inst_names):
            continue
        adj = adjusted.get(name, 0.0)
        if adj <= 0:
            continue
        matches.append(
            {
                "name": name,
                "affiliation": affiliation,
                "homepage": prof_row.get("homepage", "") or "",
                "source": "csrankings",
                "adjusted_count": round(adj, 2),
            }
        )

    matches.sort(key=lambda m: m["adjusted_count"], reverse=True)
    return matches[: max(1, min(int(limit), 20))]


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

    Uses Postgres full-text search: stopwords and stemming are handled by
    the ``english`` dictionary, and results are ranked by ``ts_rank``.
    """
    from sqlalchemy import text

    from backbone.db.session import async_session_factory

    if not query or not query.strip():
        return []
    q = query.strip()
    ts = _tsvector_expr(["name", "affiliation"])

    factory = async_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT name, affiliation, homepage_url, added_at,"
                f" ts_rank({ts}, plainto_tsquery('english', :q)) AS rank"
                f" FROM professors WHERE {ts} @@ plainto_tsquery('english', :q)"
                " ORDER BY rank DESC, added_at DESC"
                " LIMIT :limit"
            ),
            {"q": q, "limit": max(1, min(int(limit), 50))},
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

    Keyword matching uses Postgres full-text search (ranked); ``region``
    is an exact match on the stored region field.
    """
    from sqlalchemy import text

    from backbone.db.session import async_session_factory

    ts = _tsvector_expr(["title", "organization", "description"])
    conditions: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 50))}

    has_query = bool(query and query.strip())
    if has_query:
        conditions.append(f"{ts} @@ plainto_tsquery('english', :q)")
        params["q"] = query.strip()
    if region:
        conditions.append("region = :region")
        params["region"] = region

    where = " AND ".join(conditions) if conditions else "TRUE"
    if has_query:
        order = f"ts_rank({ts}, plainto_tsquery('english', :q)) DESC, posted_at DESC NULLS LAST"
    else:
        order = "posted_at DESC NULLS LAST"

    factory = async_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT title, organization, region, location, remote_ok,"
                " application_url, posted_at"
                f" FROM job_hunter_openings WHERE {where}"
                f" ORDER BY {order}"
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
