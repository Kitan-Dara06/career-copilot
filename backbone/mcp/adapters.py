"""Adapters that expose Career Copilot data as MCP tool results.

Each adapter reads canonical data and returns a plain JSON-serializable
structure. Adapters do not perform writes and do not leak secrets.
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx
import yaml

from career_copilot.config.paths import DATA_DIR

# arXiv Atom namespace for paper search parsing.
_ARXIV_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_API = "https://export.arxiv.org/api/query"
_OPENALEX_API = "https://api.openalex.org"


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


def _fold(value: str) -> str:
    """Fold diacritics + case so "Usak" matches "Uşak", "Munchen" → "München" etc."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    ).lower()


async def discover_professors(
    name: str | None = None,
    institution: str | None = None,
    topic: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find professors from CSRankings using structured selectors.

    One or more of ``name`` / ``institution`` / ``topic`` may be set; each is
    matched deterministically instead of guessing intent from free text:

    - ``name`` — a specific professor (e.g. "Yoshua Bengio"), matched by
      whole-word, case- and diacritic-folded comparison; returned directly.
    - ``institution`` — a university or city (e.g. "Usak", "McGill"). If it
      is NOT in the CSRankings institution index, the result is an EMPTY
      list — never a fallback to generic top professors.
    - ``topic`` — a research area; maps to CSRankings parent areas via
      ``_AREA_KEYWORDS``. When absent, every CS area is considered.

    The upstream CSVs are downloaded once per process and cached.
    """
    from backbone.tools.csrankings import (
        CONFERENCE_TO_PARENT,
        _load_author_info,
        _load_institutions,
        _load_prof_index,
    )

    fold = _fold

    def _tokens(value: str | None) -> list[str]:
        if not value:
            return []
        return [
            t.strip(" ,.!?;:'\"()[]")
            for t in value.split()
            if len(t.strip(" ,.!?;:'\"()[]")) > 2
        ]

    def _cap(n: int) -> int:
        return max(1, min(int(n), 20))

    name_tokens = _tokens(name)
    inst_tokens = _tokens(institution)

    # Research-topic hint → CSRankings parent areas.
    areas: set[str] = set()
    if topic:
        folded_topic = fold(topic)
        for kw, area in _AREA_KEYWORDS.items():
            if kw in folded_topic:
                areas.add(area)

    institutions = await _load_institutions()
    inst_names: list[str] = []
    if inst_tokens:
        inst_names = sorted(
            (n for n in institutions if any(fold(t) in fold(n) for t in inst_tokens)),
            key=len,
            reverse=True,
        )
        if not inst_names:
            # A named place we do not track — honest empty, not a top-N dump.
            return []

    author_info = await _load_author_info()
    prof_index = await _load_prof_index()

    # Adjusted pub counts per author. ``adjusted_all`` spans every venue for
    # ranking a specific person; ``adjusted`` is scoped by ``areas`` and falls
    # back to all venues when the topic maps to no area.
    adjusted_all: dict[str, float] = {}
    adjusted: dict[str, float] = {}
    for row_area, pname, adj in author_info["rows"]:
        parent = CONFERENCE_TO_PARENT.get(row_area)
        if parent is None:
            continue
        adjusted_all[pname] = adjusted_all.get(pname, 0.0) + adj
        if not areas or parent in areas:
            adjusted[pname] = adjusted.get(pname, 0.0) + adj

    def _affiliation(prof_row: dict[str, Any]) -> str:
        return (prof_row.get("affiliation") or "").strip()

    # 1) Specific person-name request → return that person directly.
    if name_tokens:
        matches: list[dict[str, Any]] = []
        for prof_row in prof_index:
            pname = prof_row.get("name", "")
            affiliation = _affiliation(prof_row)
            if not pname or not affiliation:
                continue
            if inst_names and not any(fold(inst) in fold(affiliation) for inst in inst_names):
                continue
            words = {fold(w) for w in pname.split()}
            if not words or not all(any(fold(t) == w for w in words) for t in name_tokens):
                continue
            matches.append(
                {
                    "name": pname,
                    "affiliation": affiliation,
                    "homepage": prof_row.get("homepage", "") or "",
                    "source": "csrankings",
                    "adjusted_count": round(adjusted_all.get(pname, 0.0), 2),
                }
            )
        matches.sort(key=lambda m: m["adjusted_count"], reverse=True)
        return matches[:_cap(limit)]

    # 2) Institution and/or topic search.
    matches: list[dict[str, Any]] = []
    for prof_row in prof_index:
        pname = prof_row.get("name", "")
        affiliation = _affiliation(prof_row)
        if not pname or not affiliation:
            continue
        if inst_names and not any(fold(inst) in fold(affiliation) for inst in inst_names):
            continue
        adj = adjusted.get(pname, 0.0)
        if adj <= 0:
            continue
        matches.append(
            {
                "name": pname,
                "affiliation": affiliation,
                "homepage": prof_row.get("homepage", "") or "",
                "source": "csrankings",
                "adjusted_count": round(adj, 2),
            }
        )

    matches.sort(key=lambda m: m["adjusted_count"], reverse=True)
    return matches[:_cap(limit)]


def _first_inst(authorship: dict[str, Any]) -> str:
    """First institution display name for an OpenAlex authorship, if any."""
    insts = authorship.get("institutions") or []
    return insts[0].get("display_name", "") if insts else ""


async def discover_professors_web(
    institution: str,
    topic: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Find professors on the web, cross-referenced so the data is attributable.

    Two independent layers, each carrying provenance on the row:

    - ``openalex`` (authoritative, no key): resolves the institution via the
      OpenAlex institutions API, then pulls its authors' works matching
      ``topic``. Authors with the most matching works are returned with
      ``verified_by_scholar: true`` and their OpenAlex author URL.
    - ``web`` (Tavily, only if ``TAVILY_API_KEY`` is set): a web search for
      faculty pages, each row tagged ``source: web`` with a clickable URL so
      the user can corroborate. Never authoritative — labelled ``verified_:false``.

    An institution that OpenAlex does not resolve yields only the web layer
    (or an empty result), never fabricated faculty.
    """
    from career_copilot.config import get_settings

    settings = get_settings()
    results: list[dict[str, Any]] = []
    resolved: dict[str, Any] = {}
    sources = ["openalex"]

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{_OPENALEX_API}/institutions",
                params={"search": institution, "per-page": "5"},
            )
            resp.raise_for_status()
            matches = (resp.json().get("results") or [])
        except httpx.HTTPError:
            matches = []

        if matches:
            inst = matches[0]
            resolved = {
                "name": inst.get("display_name", institution),
                "openalex_id": inst.get("id", ""),
            }

            # Resolve "retrieval" precisely: only use OpenAlex concepts whose
            # canonical name overlaps the topic — otherwise free-text picks
            # the wrong sense (memory / biology). For known topics we pin the
            # concept directly; for unknown topics we fall back to free-text.
            concept_id: str | None = None
            topic_norm = topic.lower().strip() if topic else ""
            if topic_norm:
                try:
                    resp = await client.get(
                        f"{_OPENALEX_API}/concepts",
                        params={"search": topic_norm, "per-page": "10"},
                    )
                    resp.raise_for_status()
                    for c in (resp.json().get("results") or []):
                        canonical = (c.get("display_name") or "").lower()
                        if topic_norm in canonical or canonical in topic_norm:
                            concept_id = c.get("id")
                            break
                except httpx.HTTPError:
                    concept_id = None

            works_params: dict[str, Any] = {
                "filter": f"institutions.id:{inst.get('id', '')}",
                "per-page": "50",
                "select": "display_name,authorships",
            }
            if concept_id:
                works_params["filter"] += f",concepts.id:{concept_id}"
            elif topic:
                works_params["search"] = topic
            try:
                resp = await client.get(f"{_OPENALEX_API}/works", params=works_params)
                resp.raise_for_status()
                works = (resp.json().get("results") or [])
            except httpx.HTTPError:
                works = []

            counts: dict[str, int] = {}
            aff: dict[str, str] = {}
            aid: dict[str, str] = {}
            for work in works:
                for au in (work.get("authorships") or []):
                    author = au.get("author") or {}
                    name = author.get("display_name", "")
                    if not name:
                        continue
                    counts[name] = counts.get(name, 0) + 1
                    aid.setdefault(name, author.get("id", ""))
                    aff.setdefault(name, _first_inst(au))

            for name, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]:
                results.append(
                    {
                        "name": name,
                        "affiliation": aff.get(name) or resolved["name"],
                        "role_hint": "",
                        "source": "openalex",
                        "verified_by_scholar": True,
                        "works_this_topic": count,
                        "url": aid.get(name, ""),
                    }
                )

        # Tavily web corroboration (optional; needs TAVILY_API_KEY).
        if settings.tavily_api_key:
            sources.append("tavily:web")
            try:
                query = f"{institution} {topic or ''} professor faculty".strip()
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": settings.tavily_api_key,
                        "query": query,
                        "max_results": max(1, min(int(limit), 5)),
                    },
                )
                resp.raise_for_status()
                for item in (resp.json().get("results") or []):
                    results.append(
                        {
                            "name": "",
                            "affiliation": institution,
                            "role_hint": (item.get("title") or "")[:140],
                            "source": "web",
                            "verified_by_scholar": False,
                            "works_this_topic": 0,
                            "url": item.get("url", ""),
                        }
                    )
            except httpx.HTTPError:
                pass

    web_limit = min(5, int(limit)) if settings.tavily_api_key else 0
    return {
        "institution": resolved or {"name": institution, "openalex_id": ""},
        "query": {"institution": institution, "topic": topic},
        "results": results[: max(1, min(int(limit), 20)) + web_limit],
        "tavily_checked": bool(settings.tavily_api_key),
        "provenance": {
            "sources": sources,
            "retrieved_at": datetime.now(UTC).isoformat(),
        },
    }


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

    Includes a ``provenance`` block (source files, retrieval timestamp, and a
    content hash) so consumers can cite the source and detect staleness.
    """
    profile = _load_yaml("user_profile.yaml")
    skills_raw = _load_yaml("user_skills.yaml")
    skills = skills_raw.get("skills", {}) or {}

    body = {
        "research_interests": (profile.get("research_interests") or "").strip(),
        "keywords": profile.get("keywords") or [],
        "arxiv_categories": profile.get("arxiv_categories") or [],
        "preferences": profile.get("preferences") or {},
        "skill_clusters": [
            {
                "name": name,
                "skills": cluster.get("skills") or [],
                "weight": cluster.get("weight", 1.0),
            }
            for name, cluster in skills.items()
        ],
    }
    body["provenance"] = {
        "sources": ["data/user_profile.yaml", "data/user_skills.yaml"],
        "retrieved_at": datetime.now(UTC).isoformat(),
        # Content hash for staleness detection: recompute against the files.
        "version_key": hashlib.sha256(str(body).encode("utf-8")).hexdigest()[:16],
    }
    return body


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
        published = entry.find(f"{_ARXIV_NS}published").text
        papers.append(
            {
                "arxiv_id": arxiv_id,
                "title": title,
                "authors": authors[:3],
                # Short excerpt keeps `career.papers.search` output clean for chat.
                "abstract": summary[:180] + ("…" if len(summary) > 180 else ""),
                "published": published,
                "year": (published or "")[:4],
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
