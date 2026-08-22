"""Career Copilot MCP server — stdio entry point.

Exposes a curated, read-only subset of Career Copilot data to Hermes Agent.

Run standalone for debugging:

    uv run python -m backbone.mcp.server

Hermes launches this same command via its MCP config (see deploy/hermes/).
"""

from __future__ import annotations

import logging
import sys

# ── Stdio-transport hygiene (must run before any tool module is imported) ──
# The stdio MCP transport reserves stdout for JSON-RPC messages only. Tool
# registration logs (backbone.tools.registry.register → structlog) fire at
# import time, and if structlog prints to stdout it corrupts the client's
# JSON-RPC parser (Hermes: "Failed to parse JSONRPC message from server").
# Pin the stdlib root handler to stderr and route structlog through stdlib
# logging so application logs can never leak onto the transport. Without
# ``force=True`` this is a no-op when pytest has already configured a root
# handler, so test log capture is untouched.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)

from career_copilot.config.logging import configure_logging  # noqa: E402

configure_logging(json_output=False)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from .adapters import (
    discover_professors,
    discover_professors_web,
    load_profile,
    search_jobs,
    search_papers,
    search_professors,
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
from .planning_writes import (
    propose_add_goal,
    propose_add_note,
    propose_add_task,
    propose_create_workspace,
    propose_record_decision,
    propose_supersede_decision,
    propose_switch_workspace,
    propose_update_task_status,
)
from .policy import apply_policy

class _LoggingFastMCP(FastMCP):
    """FastMCP that records every tool call to ``hermes_tool_calls`` (§15).

    Tool-level observability lives here because this server is where Hermes's
    tool invocations are actually observable: name, args, latency, outcome.
    ``run_id`` stays NULL until Hermes run events are wired up (its
    chat/completions response does not expose the internal tool transcript).
    """

    async def call_tool(
        self, name: str, arguments: dict[str, object] | None
    ) -> object:
        import time as _time

        from backbone.hermes_observability import (
            HermesToolCall,
            spawn_log_tool_call,
            summarize,
            summarize_args,
        )

        started = _time.perf_counter()
        result: object = None
        exc_text: str | None = None
        try:
            result = await super().call_tool(name, arguments)
            return result
        except Exception as exc:
            exc_text = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            latency_ms = int((_time.perf_counter() - started) * 1000)
            spawn_log_tool_call(
                HermesToolCall(
                    tool_name=name,
                    args=summarize_args(dict(arguments or {})),
                    output_excerpt=summarize(exc_text if exc_text is not None else result),
                    latency_ms=latency_ms,
                    outcome="error" if exc_text is not None else "success",
                )
            )


mcp = _LoggingFastMCP("career-copilot")


def _profile_query_default() -> str:
    """Build a default professor-search query from stored research interests.

    Used when Hermes calls ``career.professors.search`` without a ``query`` so
    the tool still returns relevant faculty instead of an empty result.
    """
    profile = load_profile()
    keywords = profile.get("keywords") or []
    if keywords:
        return " ".join(str(k) for k in keywords[:6])
    interests = (profile.get("research_interests") or "").strip()
    return interests[:80] if interests else ""


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

    Returns a compact list with id, title, authors, a short abstract excerpt,
    year, and arXiv URL. Read-only.
    """
    return apply_policy(await search_papers(query, limit))


@mcp.tool(name="career.professors.search")
async def career_professors_search(
    name: str | None = None,
    institution: str | None = None,
    topic: str | None = None,
    limit: int = 10,
) -> dict:
    """Search professors — your watchlist first, then CSRankings faculty.

    Watchlist matches are tagged ``source: watchlist``. CSRankings discovery
    takes structured selectors — extract what the user named: a PERSON goes in
    ``name``, a UNIVERSITY/CITY in ``institution``, a research AREA in
    ``topic``. Combine them when the user does (e.g. institution="McGill",
    topic="retrieval").

    Args:
        name: Optional professor name, e.g. "Yoshua Bengio".
        institution: Optional university or city, e.g. "Usak", "McGill".
            If named but absent from our faculty index, no faculty match.
        topic: Optional research area, e.g. "retrieval", "agent memory".
        limit: Max total results (1-50, default 10).

    If NO selector is given, the user's stored research interests are used as
    the topic — never ask the user to restate their interests.

    When the curated index has NO faculty for a named institution (some labs
    sit outside CSRankings — e.g. iSchool / Faculty-of-Information IR
    researchers), call career.professors.web_search with the same institution
    (+ topic) to find verified faculty on the web.
    Read-only.
    """
    if not (name or institution or topic):
        topic = _profile_query_default()

    q_part = " ".join(x for x in (name, institution, topic) if x)
    watchlist = await search_professors(q_part, limit)
    discovered = await discover_professors(
        name=name,
        institution=institution,
        topic=topic,
        limit=limit,
    )

    # Auto-escalate to the web layer when a named institution yields thin (or
    # empty) curated results — iSchool / Faculty-of-Information researchers sit
    # outside CSRankings. OpenAlex rows are merged into the main results; the
    # Tavily web hits (e.g. a Faculty of Information directory page) are
    # returned separately as ``web_corroboration`` so they are not swallowed
    # by the name-dedup (they lack a structured name).
    payload: dict = {}
    web_hits: list[dict] = []
    if institution and len(discovered) < 3:
        web = await discover_professors_web(institution, topic, limit=limit)
        known_names = {x["name"].lower() for x in discovered if x.get("name")}
        for p in web.get("results") or []:
            if p.get("source") == "web":
                web_hits.append(p)
            elif (
                p.get("source") == "openalex"
                and p.get("name")
                and p["name"].lower() not in known_names
            ):
                known_names.add(p["name"].lower())
                discovered.append(p)

    merged: list[dict] = [{"source": "watchlist", **p} for p in watchlist]
    seen = {p["name"].lower() for p in merged}
    for p in discovered:
        if p["name"].lower() in seen:
            continue
        seen.add(p["name"].lower())
        merged.append(p)
    payload["results"] = merged[: max(1, min(int(limit), 50))]
    if web_hits:
        payload["web_corroboration"] = web_hits[: max(1, min(int(limit), 5))]
    return apply_policy(payload)


@mcp.tool(name="career.professors.web_search")
async def career_professors_web_search(
    institution: str,
    topic: str | None = None,
    limit: int = 10,
) -> dict:
    """Find professors on the web, cross-referenced against OpenAlex.

    Use this when the curated CSRankings index has no faculty for an
    institution (e.g. iSchool / Faculty-of-Information researchers not ranked
    by CS venues). Results carry provenance on every row:

      - ``openalex`` rows are ``verified_by_scholar: true`` with the author's
        OpenAlex profile URL (they publish at that institution on the topic).
      - ``web`` rows come from Tavily and carry a clickable URL; they are
        corroboration, not verified facts.

    Args:
        institution: University or city, e.g. "University of Toronto".
        topic: Optional research area, e.g. "retrieval".
        limit: Max results (1-20, default 10).

    Read-only.
    """
    return apply_policy(await discover_professors_web(institution, topic, limit))


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


# ── Planning write tools (Phase 2, §5) — proposals, never direct writes ──
# Every write returns/creates a ``planning_proposals`` row. Low-risk writes
# apply immediately; medium/high-risk stay ``pending`` until the user approves
# them (Telegram inline-button /approve flow). Tell the user their change is
# queued and to approve it.


@mcp.tool(name="career.planning.create_workspace")
async def career_planning_create_workspace(
    name: str,
    intake_year: int,
    target_degree: str,
    owner: str = "aaliyah",
) -> dict:
    """Propose creating a new planning workspace (e.g. "Master's 2027").

    High-risk: this returns a pending proposal that must be approved. Do NOT
    claim the workspace was created. Reply with: "📝 Queued (high): Create
    workspace <name> — Approve: /approve <proposal_id>".

    Args:
        name: Workspace name, e.g. "Master's 2027".
        intake_year: Year of intake, e.g. 2027.
        target_degree: e.g. "MSc" or "PhD".
        owner: Owner id (defaults to the single user).
    """
    return apply_policy(
        await propose_create_workspace(name, intake_year, target_degree, owner=owner)
    )


@mcp.tool(name="career.planning.add_goal")
async def career_planning_add_goal(
    workspace_id: int,
    title: str,
    description: str | None = None,
    priority: int = 0,
    parent_id: int | None = None,
) -> dict:
    """Propose adding a strategic goal to a workspace.

    Returns a pending proposal (medium risk) that must be approved. Do NOT
    claim the goal was added. Reply with exactly: "📝 Queued (medium): Add goal:
    <title> — Approve: /approve <proposal_id>  Skip: /skip <proposal_id>".

    Args:
        workspace_id: Target workspace id.
        title: Goal title, e.g. "Funding strategy".
        description: Optional detail.
        priority: Higher first (default 0).
        parent_id: Optional parent goal id.
    """
    return apply_policy(
        await propose_add_goal(workspace_id, title, description, priority, parent_id)
    )


@mcp.tool(name="career.planning.add_task")
async def career_planning_add_task(
    workspace_id: int,
    goal_id: int,
    title: str,
    description: str | None = None,
    due_date: str | None = None,
    status: str = "todo",
) -> dict:
    """Propose adding a task under a goal.

    Returns a pending proposal (medium risk) that must be approved. Do NOT
    claim the task was added. Reply with: "📝 Queued (medium): Add task: <title>
    — Approve: /approve <proposal_id>".

    Args:
        workspace_id: Target workspace id.
        goal_id: Parent goal id.
        title: Task title.
        description: Optional detail.
        due_date: Optional ISO date (YYYY-MM-DD).
        status: todo | doing | blocked | done.
    """
    return apply_policy(
        await propose_add_task(workspace_id, goal_id, title, description, due_date, status)
    )


@mcp.tool(name="career.planning.record_decision")
async def career_planning_record_decision(
    workspace_id: int,
    title: str,
    rationale: str | None = None,
    evidence: dict | None = None,
    status: str = "proposed",
) -> dict:
    """Propose recording a decision with rationale and evidence.

    Returns a pending proposal (medium risk) that must be approved. Do NOT
    claim the decision was recorded. Reply with: "📝 Queued (medium): Record
    decision: <title> — Approve: /approve <proposal_id>".

    Args:
        workspace_id: Target workspace id.
        title: Decision title, e.g. "Apply primarily to Canada".
        rationale: Why.
        evidence: Provenance dict (sources / retrieved_at).
        status: idea | recommendation | proposed (default proposed).
    """
    return apply_policy(
        await propose_record_decision(workspace_id, title, rationale, evidence, status)
    )


@mcp.tool(name="career.planning.supersede_decision")
async def career_planning_supersede_decision(
    decision_id: int,
    title: str,
    rationale: str | None = None,
) -> dict:
    """Propose superseding an old decision with a new confirmed one.

    Returns a pending proposal (medium risk) that must be approved.

    Args:
        decision_id: The decision to mark superseded.
        title: New decision title replacing it.
        rationale: Why the change.
    """
    return apply_policy(await propose_supersede_decision(decision_id, title, rationale))


@mcp.tool(name="career.planning.update_task_status")
async def career_planning_update_task_status(
    task_id: int,
    status: str,
) -> dict:
    """Propose updating a task's status (todo/doing/blocked/done).

    Low risk: applied immediately.

    Args:
        task_id: Target task id.
        status: One of todo | doing | blocked | done.
    """
    return apply_policy(await propose_update_task_status(task_id, status))


@mcp.tool(name="career.planning.add_note")
async def career_planning_add_note(
    workspace_id: int,
    body: str,
    kind: str = "note",
    pinned: bool = False,
) -> dict:
    """Propose adding a free-form note to a workspace.

    Low risk: applied immediately.

    Args:
        workspace_id: Target workspace id.
        body: Note text.
        kind: note | observation | decision_note.
        pinned: Pin to top (default false).
    """
    return apply_policy(await propose_add_note(workspace_id, body, kind, pinned))


@mcp.tool(name="career.planning.switch_workspace")
async def career_planning_switch_workspace(
    workspace_id: int,
    chat_id: str = "aaliyah",
) -> dict:
    """Propose switching the active workspace for the user.

    Low risk: applied immediately.

    Args:
        workspace_id: The workspace to make active.
        chat_id: Chat/user id (defaults to the single user).
    """
    return apply_policy(await propose_switch_workspace(workspace_id, chat_id=chat_id))


if __name__ == "__main__":
    mcp.run()
