"""Planning workspace adapters — the read side of Phase 2.

Exposes workspace, goal, task, decision, note, artifact, and state data to
the Career Copilot MCP server. Write operations live in
``planning_writes.py`` and return proposal objects (Telegram inline-button
flow) instead of writing directly.

Conventions:
- All return dicts include a ``provenance`` block (sources / retrieved_at /
  version_key) so consumers can cite the data and detect staleness.
- ISO timestamps for everything time-related.
- Lists return plain dicts that serialize to JSON for Telegram / MCP.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _provenance(sources: list[str]) -> dict[str, Any]:
    """Build a provenance block for any read response."""
    return {
        "sources": sources,
        "retrieved_at": _now_iso(),
        "version_key": hashlib.sha256(",".join(sources).encode()).hexdigest()[:16],
    }


def _iso(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _parse_date(value: str) -> date:
    """Parse an ISO date (or datetime) string into a ``date`` for DATE binds."""
    return date.fromisoformat(value[:10])


def _session_factory():
    """Return a sessionmaker (lazy import so tests can patch it).

    Callers use ``async with _session_factory()() as session`` — the outer
    call returns the sessionmaker, the inner call returns an AsyncSession.
    """
    from backbone.db.session import async_session_factory

    return async_session_factory()


async def get_active_workspace_id(chat_id: str) -> int | None:
    """Return the workspace id currently active for ``chat_id``, or None."""
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT active_workspace_id FROM planning_state"
                " WHERE chat_id = :chat"
            ),
            {"chat": chat_id},
        )
        row = result.one_or_none()
        return row.active_workspace_id if row else None


async def list_workspaces(owner: str = "aaliyah") -> list[dict[str, Any]]:
    """List all workspaces for the owner (default: the single user)."""
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT id, name, intake_year, target_degree, owner, status, created_at"
                " FROM planning_workspaces WHERE owner = :owner"
                " ORDER BY created_at DESC"
            ),
            {"owner": owner},
        )
        rows = result.all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "intake_year": r.intake_year,
            "target_degree": r.target_degree,
            "owner": r.owner,
            "status": r.status,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def get_workspace(workspace_id: int) -> dict[str, Any] | None:
    """Return one workspace, or None if it doesn't exist."""
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT id, name, intake_year, target_degree, owner, status, created_at"
                " FROM planning_workspaces WHERE id = :id"
            ),
            {"id": workspace_id},
        )
        row = result.one_or_none()
    if not row:
        return None
    ws = {
        "id": row.id,
        "name": row.name,
        "intake_year": row.intake_year,
        "target_degree": row.target_degree,
        "owner": row.owner,
        "status": row.status,
        "created_at": _iso(row.created_at),
    }
    ws["provenance"] = _provenance([f"planning_workspaces:id={workspace_id}"])
    return ws


async def list_goals(
    workspace_id: int,
    status: str | None = None,
) -> list[dict[str, Any]]:
    async with _session_factory()() as session:
        params: dict[str, Any] = {"wid": workspace_id}
        where = "workspace_id = :wid"
        if status:
            where += " AND status = :status"
            params["status"] = status
        result = await session.execute(
            text(
                "SELECT id, workspace_id, title, description, parent_id,"
                " priority, status, created_at"
                f" FROM planning_goals WHERE {where}"
                " ORDER BY priority DESC, created_at"
            ),
            params,
        )
        rows = result.all()
    return [
        {
            "id": r.id,
            "workspace_id": r.workspace_id,
            "title": r.title,
            "description": r.description,
            "parent_id": r.parent_id,
            "priority": r.priority,
            "status": r.status,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def list_tasks(
    workspace_id: int,
    status: str | None = None,
    due_before: str | None = None,
) -> list[dict[str, Any]]:
    """List tasks, optionally filtered by status and ISO date string (YYYY-MM-DD)."""
    params: dict[str, Any] = {"wid": workspace_id}
    where = "workspace_id = :wid"
    if status:
        where += " AND status = :status"
        params["status"] = status
    if due_before:
        where += " AND due_date IS NOT NULL AND due_date <= :due"
        params["due"] = _parse_date(due_before)
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT id, goal_id, workspace_id, title, description, due_date,"
                " status, blocked_by_task_id, created_at, updated_at"
                f" FROM planning_tasks WHERE {where}"
                " ORDER BY due_date NULLS LAST, created_at DESC"
            ),
            params,
        )
        rows = result.all()
    return [
        {
            "id": r.id,
            "goal_id": r.goal_id,
            "workspace_id": r.workspace_id,
            "title": r.title,
            "description": r.description,
            "due_date": _iso(r.due_date),
            "status": r.status,
            "blocked_by_task_id": r.blocked_by_task_id,
            "created_at": _iso(r.created_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


async def list_decisions(
    workspace_id: int,
    status: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"wid": workspace_id}
    where = "workspace_id = :wid"
    if status:
        where += " AND status = :status"
        params["status"] = status
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT id, workspace_id, title, rationale, status, evidence,"
                " decided_at, superseded_by_id"
                f" FROM planning_decisions WHERE {where}"
                " ORDER BY decided_at DESC"
            ),
            params,
        )
        rows = result.all()
    return [
        {
            "id": r.id,
            "workspace_id": r.workspace_id,
            "title": r.title,
            "rationale": r.rationale,
            "status": r.status,
            "evidence": r.evidence,
            "decided_at": _iso(r.decided_at),
            "superseded_by_id": r.superseded_by_id,
        }
        for r in rows
    ]


async def list_notes(workspace_id: int) -> list[dict[str, Any]]:
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT id, workspace_id, kind, body, pinned, created_at"
                " FROM planning_notes WHERE workspace_id = :wid"
                " ORDER BY pinned DESC, created_at DESC"
            ),
            {"wid": workspace_id},
        )
        rows = result.all()
    return [
        {
            "id": r.id,
            "workspace_id": r.workspace_id,
            "kind": r.kind,
            "body": r.body,
            "pinned": r.pinned,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def list_artifacts(
    workspace_id: int,
    artifact_type: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"wid": workspace_id}
    where = "workspace_id = :wid"
    if artifact_type:
        where += " AND type = :t"
        params["t"] = artifact_type
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT id, workspace_id, type, title, body, evidence,"
                " version, status, created_at, updated_at"
                f" FROM planning_artifacts WHERE {where}"
                " ORDER BY updated_at DESC"
            ),
            params,
        )
        rows = result.all()
    return [
        {
            "id": r.id,
            "workspace_id": r.workspace_id,
            "type": r.type,
            "title": r.title,
            "body": r.body,
            "evidence": r.evidence,
            "version": r.version,
            "status": r.status,
            "created_at": _iso(r.created_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


async def get_summary(workspace_id: int) -> dict[str, Any]:
    """Compact workspace summary — the session-bootstrap payload.

    This is what the bridge prepends to the first user message on a new
    chat so Hermes has the current state without chat history.
    """
    ws = await get_workspace(workspace_id)
    if not ws:
        return {"error": f"Workspace {workspace_id} not found"}

    goals = await list_goals(workspace_id)
    open_goals = [g for g in goals if g["status"] == "open"]
    tasks = await list_tasks(workspace_id)
    overdue_tasks = [
        t
        for t in tasks
        if t["status"] in ("todo", "doing") and t["due_date"] and t["due_date"] < _now_iso()[:10]
    ]
    decisions = await list_decisions(workspace_id)
    confirmed = [d for d in decisions if d["status"] == "confirmed"]
    summary = {
        "workspace": ws,
        "open_goals_count": len(open_goals),
        "open_goals_titles": [g["title"] for g in open_goals[:5]],
        "overdue_tasks_count": len(overdue_tasks),
        "overdue_tasks_titles": [t["title"] for t in overdue_tasks[:5]],
        "confirmed_decisions_titles": [d["title"] for d in confirmed[:5]],
        "total_tasks_open": sum(1 for t in tasks if t["status"] in ("todo", "doing")),
        "last_active_at": _now_iso(),
    }
    summary["provenance"] = _provenance([f"planning_workspaces:id={workspace_id}"])
    return summary
