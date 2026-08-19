"""Career Copilot MCP server — stdio entry point.

Exposes a curated, read-only subset of Career Copilot data to Hermes Agent.

Run standalone for debugging:

    uv run python -m backbone.mcp.server

Hermes launches this same command via its MCP config (see deploy/hermes/).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .adapters import (
    discover_professors,
    load_profile,
    search_jobs,
    search_papers,
    search_professors,
    should_discover,
)
from .planning import (
    get_active_workspace_id,
    get_summary,
    get_workspace,
    list_artifacts,
    list_decisions,
    list_goals,
    list_notes,
    list_tasks,
    list_workspaces,
)
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
    """Search professors — your watchlist first, then CSRankings faculty.

    Watchlist matches are tagged ``source: watchlist``. If the query names
    an institution and/or research area (e.g. "McGill doing retrieval"),
    CSRankings faculty matching both are returned too, tagged
    ``source: csrankings`` and ranked by publication activity.

    Args:
        query: Name, affiliation, institution, and/or area (e.g. "McGill retrieval").
        limit: Max total results (1-50, default 10).

    Read-only.
    """
    watchlist = await search_professors(query, limit)
    discovered: list[dict] = []
    if should_discover(query):
        discovered = await discover_professors(query, limit)

    merged: list[dict] = [{"source": "watchlist", **p} for p in watchlist]
    seen = {p["name"].lower() for p in merged}
    for p in discovered:
        if p["name"].lower() in seen:
            continue
        seen.add(p["name"].lower())
        merged.append(p)

    return apply_policy(merged[: max(1, min(int(limit), 50))])


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


# ── Planning read tools (Phase 2, §5) ────────────────────────────


@mcp.tool(name="career.planning.list_workspaces")
async def career_planning_list_workspaces() -> dict:
    """List all planning workspaces for the owner.

    Returns name, intake year, target degree, and status for each.
    Read-only.
    """
    return apply_policy(await list_workspaces())


@mcp.tool(name="career.planning.get_workspace")
async def career_planning_get_workspace(workspace_id: int) -> dict:
    """Return one workspace by id, or an error dict if not found.

    Read-only.
    """
    ws = await get_workspace(workspace_id)
    if not ws:
        return {"error": f"workspace {workspace_id} not found"}
    return apply_policy(ws)


@mcp.tool(name="career.planning.list_goals")
async def career_planning_list_goals(
    workspace_id: int,
    status: str | None = None,
) -> dict:
    """List goals in a workspace, optionally filtered by status.

    Args:
        workspace_id: Required workspace id.
        status: Optional filter ('open', 'done', 'dropped').

    Read-only.
    """
    return apply_policy(await list_goals(workspace_id, status))


@mcp.tool(name="career.planning.list_tasks")
async def career_planning_list_tasks(
    workspace_id: int,
    status: str | None = None,
    due_before: str | None = None,
) -> dict:
    """List tasks in a workspace, optionally filtered.

    Args:
        workspace_id: Required workspace id.
        status: Optional filter ('todo', 'doing', 'blocked', 'done').
        due_before: Optional ISO date (YYYY-MM-DD) — only tasks due on or before.

    Read-only.
    """
    return apply_policy(await list_tasks(workspace_id, status, due_before))


@mcp.tool(name="career.planning.list_decisions")
async def career_planning_list_decisions(
    workspace_id: int,
    status: str | None = None,
) -> dict:
    """List decisions in a workspace, optionally filtered.

    Args:
        workspace_id: Required workspace id.
        status: Optional filter ('idea', 'recommendation', 'proposed',
            'confirmed', 'superseded').

    Returns decisions with rationale, evidence, and lifecycle status.
    Read-only.
    """
    return apply_policy(await list_decisions(workspace_id, status))


@mcp.tool(name="career.planning.list_notes")
async def career_planning_list_notes(workspace_id: int) -> dict:
    """List notes in a workspace, pinned first.

    Read-only.
    """
    return apply_policy(await list_notes(workspace_id))


@mcp.tool(name="career.planning.list_artifacts")
async def career_planning_list_artifacts(
    workspace_id: int,
    artifact_type: str | None = None,
) -> dict:
    """List artifacts in a workspace, optionally filtered by type.

    Args:
        workspace_id: Required workspace id.
        artifact_type: Optional filter (e.g. 'reading_plan',
            'school_application', 'school_comparison').

    Read-only.
    """
    return apply_policy(await list_artifacts(workspace_id, artifact_type))


@mcp.tool(name="career.planning.get_summary")
async def career_planning_get_summary(
    chat_id: str,
    workspace_id: int | None = None,
) -> dict:
    """Get a compact workspace summary for the active or specified workspace.

    This is the session-bootstrap payload that the bridge prepends to
    the first user message of a new chat. Use ``chat_id`` to look up the
    active workspace for that chat; pass ``workspace_id`` to override.

    Args:
        chat_id: The Telegram chat id (so we can find the active workspace).
        workspace_id: Optional explicit workspace id (skips the active
            lookup).

    Returns counts and titles of open goals, overdue tasks, and
    confirmed decisions. Read-only.
    """
    wid = workspace_id if workspace_id is not None else await get_active_workspace_id(chat_id)
    if wid is None:
        return {"error": "no active workspace for chat_id and no workspace_id given"}
    return apply_policy(await get_summary(wid))


if __name__ == "__main__":
    mcp.run()
