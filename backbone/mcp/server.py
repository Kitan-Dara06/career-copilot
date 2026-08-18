"""Career Copilot MCP server — stdio entry point.

Exposes a curated, read-only subset of Career Copilot data to Hermes Agent.

Run standalone for debugging:

    uv run python -m backbone.mcp.server

Hermes launches this same command via its MCP config (see deploy/hermes/).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .adapters import load_profile, search_jobs, search_papers, search_professors
from .policy import apply_policy

mcp = FastMCP("career-copilot")


@mcp.tool(name="career.profile.get")
def career_profile_get() -> dict:
    """Get the user's canonical profile.

    Returns research interests, keywords, arXiv categories, preferences,
    and the weighted skill clusters used for matching across agents.
    Read-only; never writes.
    """
    return apply_policy(load_profile())


@mcp.tool(name="career.papers.search")
async def career_papers_search(query: str, limit: int = 5) -> dict:
    """Search recent arXiv papers by keyword, newest first.

    Args:
        query: Free-text keyword query (e.g. "RAG evaluation").
        limit: Max papers to return (1-20, default 5).

    Returns a compact list with id, title, authors, abstract excerpt,
    publish date, and URL. Read-only.
    """
    return apply_policy(await search_papers(query, limit))


@mcp.tool(name="career.professors.search")
async def career_professors_search(query: str, limit: int = 10) -> dict:
    """Search the professor watchlist by name or affiliation.

    Args:
        query: Name or affiliation fragment (e.g. "McGill").
        limit: Max results (1-50, default 10).

    Returns watchlist entries with name, affiliation, homepage, and
    added date. Read-only.
    """
    return apply_policy(await search_professors(query, limit))


@mcp.tool(name="career.jobs.search")
async def career_jobs_search(
    query: str | None = None,
    region: str | None = None,
    limit: int = 10,
) -> dict:
    """Search discovered job openings by keyword and/or region.

    Args:
        query: Keyword fragment matching title, organization, or description.
        region: One of nigeria, africa, eu, canada, international_remote.
        limit: Max results (1-50, default 10).

    Returns openings with title, organization, region, location, remote
    flag, application URL, and posted date. Read-only.
    """
    return apply_policy(await search_jobs(query, region, limit))


if __name__ == "__main__":
    mcp.run()
