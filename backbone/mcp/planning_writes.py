"""Planning write adapters — the write side of Phase 2.

Every write goes through a ``planning_proposals`` row so nothing mutates the
planning tables until a user approves it (the Telegram inline-button flow).

Risk policy:
  - ``low``    (add_note, update_task_status, switch_workspace) — auto-applied
               on creation; the proposal row still records it as ``approved``.
  - ``medium`` (add_goal, add_task, record_decision, supersede_decision) —
               created ``pending`` and applied only after approval.
  - ``high``   (create_workspace) — created ``pending`` and applied after
               approval.

``apply_proposal`` is the single place that turns a pending proposal into a
real database write, so the Telegram bot and any future client share one path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

DEFAULT_CHAT = "aaliyah"
PROPOSAL_TTL_HOURS = 24

_RISK_LEVELS: dict[str, str] = {
    "planning.create_workspace": "high",
    "planning.add_goal": "medium",
    "planning.add_task": "medium",
    "planning.record_decision": "medium",
    "planning.supersede_decision": "medium",
    "planning.update_task_status": "low",
    "planning.add_note": "low",
    "planning.switch_workspace": "low",
}
_AUTO_APPLY_RISKS = {"low"}


def _session_factory():
    """Return a sessionmaker (lazy import so tests can patch it).

    Callers use ``async with _session_factory()() as session`` — the outer
    call returns the sessionmaker, the inner call an AsyncSession.
    """
    from backbone.db.session import async_session_factory

    return async_session_factory()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _provenance(sources: list[str]) -> dict[str, Any]:
    return {
        "sources": sources,
        "retrieved_at": _now_iso(),
        "version_key": hashlib.sha256(",".join(sources).encode()).hexdigest()[:16],
    }


def risk_level_for(tool: str) -> str:
    return _RISK_LEVELS.get(tool, "medium")


# ── Proposal lifecycle ───────────────────────────────────────────


async def create_proposal(
    *,
    tool: str,
    args: dict[str, Any],
    summary: str,
    workspace_id: int | None = None,
    chat_id: str = DEFAULT_CHAT,
) -> dict[str, Any]:
    """Insert a pending proposal row and (for low risk) auto-apply it."""
    risk = risk_level_for(tool)
    expires_at = datetime.now(UTC) + timedelta(hours=PROPOSAL_TTL_HOURS)
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "INSERT INTO planning_proposals"
                " (chat_id, workspace_id, tool, args, summary, risk_level, status, expires_at)"
                " VALUES (:c, :w, :t, :a, :s, :r, 'pending', :e)"
                " RETURNING id"
            ),
            {
                "c": chat_id,
                "w": workspace_id,
                "t": tool,
                "a": json.dumps(args),  # JSON column — asyncpg needs a string
                "s": summary,
                "r": risk,
                "e": expires_at,
            },
        )
        proposal_id = result.scalar_one()
        await session.commit()

    if risk in _AUTO_APPLY_RISKS:
        return await apply_proposal(proposal_id)

    return {
        "proposal_id": proposal_id,
        "tool": tool,
        "args": args,
        "summary": summary,
        "risk_level": risk,
        "status": "pending",
        "workspace_id": workspace_id,
        "chat_id": chat_id,
        "expires_at": _iso(expires_at),
        "provenance": _provenance([f"planning_proposals:id={proposal_id}"]),
    }


async def list_pending_proposals(
    chat_id: str = DEFAULT_CHAT,
) -> list[dict[str, Any]]:
    """Return pending, un-expired proposals for a chat, newest first."""
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT id, tool, args, summary, risk_level, workspace_id,"
                " created_at, expires_at"
                " FROM planning_proposals"
                " WHERE chat_id = :c AND status = 'pending'"
                "   AND expires_at > now()"
                " ORDER BY created_at DESC"
            ),
            {"c": chat_id},
        )
        rows = result.all()
    return [
        {
            "proposal_id": r.id,
            "tool": r.tool,
            "summary": r.summary,
            "risk_level": r.risk_level,
            "workspace_id": r.workspace_id,
            "created_at": _iso(r.created_at),
            "expires_at": _iso(r.expires_at),
        }
        for r in rows
    ]


async def skip_proposal(
    proposal_id: int,
    chat_id: str = DEFAULT_CHAT,
) -> dict[str, Any]:
    """Mark a pending proposal as skipped (no write happens)."""
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "UPDATE planning_proposals SET status = 'skipped'"
                " WHERE id = :id AND chat_id = :c AND status = 'pending'"
                " RETURNING id, tool, summary"
            ),
            {"id": proposal_id, "c": chat_id},
        )
        row = result.one_or_none()
        if row is None:
            await session.rollback()
            raise ValueError(f"Proposal {proposal_id} not found or already resolved")
        await session.commit()
    return {
        "proposal_id": proposal_id,
        "tool": row.tool,
        "summary": row.summary,
        "status": "skipped",
    }


async def apply_proposal(proposal_id: int) -> dict[str, Any]:
    """Apply a pending proposal: run its executor, then mark it approved."""
    async with _session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT id, chat_id, workspace_id, tool, args, summary, risk_level, status"
                " FROM planning_proposals WHERE id = :id"
            ),
            {"id": proposal_id},
        )
        p = result.one_or_none()
        if p is None:
            raise ValueError(f"Proposal {proposal_id} not found")
        if p.status != "pending":
            raise ValueError(f"Proposal {proposal_id} already {p.status}")

        executor = _EXECUTORS.get(p.tool)
        if executor is None:
            raise ValueError(f"No executor for tool {p.tool!r}")

        new_id = await executor(session, p.args)
        await session.execute(
            text("UPDATE planning_proposals SET status = 'approved' WHERE id = :id"),
            {"id": proposal_id},
        )
        await session.commit()

    return {
        "proposal_id": proposal_id,
        "tool": p.tool,
        "args": p.args,
        "summary": p.summary,
        "risk_level": p.risk_level,
        "status": "approved",
        "result_id": new_id,
        "workspace_id": p.workspace_id,
        "chat_id": p.chat_id,
        "applied_at": _now_iso(),
        "provenance": _provenance([f"planning_proposals:id={proposal_id}"]),
    }


# ── Per-tool proposal creators ────────────────────────────────────


async def propose_create_workspace(
    name: str,
    intake_year: int,
    target_degree: str,
    owner: str = DEFAULT_CHAT,
    status: str = "active",
) -> dict[str, Any]:
    return await create_proposal(
        tool="planning.create_workspace",
        args={
            "name": name,
            "intake_year": int(intake_year),
            "target_degree": target_degree,
            "owner": owner,
            "status": status,
        },
        summary=f"Create workspace {name!r} ({target_degree}, {intake_year})",
        workspace_id=None,
        chat_id=owner,
    )


async def propose_add_goal(
    workspace_id: int,
    title: str,
    description: str | None = None,
    priority: int = 0,
    parent_id: int | None = None,
    chat_id: str = DEFAULT_CHAT,
) -> dict[str, Any]:
    return await create_proposal(
        tool="planning.add_goal",
        args={
            "workspace_id": int(workspace_id),
            "title": title,
            "description": description,
            "priority": int(priority),
            "parent_id": parent_id,
        },
        summary=f"Add goal: {title}",
        workspace_id=int(workspace_id),
        chat_id=chat_id,
    )


async def propose_add_task(
    workspace_id: int,
    goal_id: int,
    title: str,
    description: str | None = None,
    due_date: str | None = None,
    status: str = "todo",
    chat_id: str = DEFAULT_CHAT,
) -> dict[str, Any]:
    return await create_proposal(
        tool="planning.add_task",
        args={
            "workspace_id": int(workspace_id),
            "goal_id": int(goal_id),
            "title": title,
            "description": description,
            "due_date": due_date,
            "status": status,
        },
        summary=f"Add task: {title}",
        workspace_id=int(workspace_id),
        chat_id=chat_id,
    )


async def propose_record_decision(
    workspace_id: int,
    title: str,
    rationale: str | None = None,
    evidence: dict[str, Any] | None = None,
    status: str = "proposed",
    chat_id: str = DEFAULT_CHAT,
) -> dict[str, Any]:
    return await create_proposal(
        tool="planning.record_decision",
        args={
            "workspace_id": int(workspace_id),
            "title": title,
            "rationale": rationale,
            "evidence": evidence,
            "status": status,
        },
        summary=f"Record decision: {title}",
        workspace_id=int(workspace_id),
        chat_id=chat_id,
    )


async def propose_supersede_decision(
    decision_id: int,
    title: str,
    rationale: str | None = None,
    chat_id: str = DEFAULT_CHAT,
) -> dict[str, Any]:
    return await create_proposal(
        tool="planning.supersede_decision",
        args={"decision_id": int(decision_id), "title": title, "rationale": rationale},
        summary=f"Supersede decision #{decision_id} with: {title}",
        workspace_id=None,
        chat_id=chat_id,
    )


async def propose_update_task_status(
    task_id: int,
    status: str,
    chat_id: str = DEFAULT_CHAT,
) -> dict[str, Any]:
    return await create_proposal(
        tool="planning.update_task_status",
        args={"task_id": int(task_id), "status": status},
        summary=f"Mark task #{task_id} as {status}",
        workspace_id=None,
        chat_id=chat_id,
    )


async def propose_add_note(
    workspace_id: int,
    body: str,
    kind: str = "note",
    pinned: bool = False,
    chat_id: str = DEFAULT_CHAT,
) -> dict[str, Any]:
    return await create_proposal(
        tool="planning.add_note",
        args={
            "workspace_id": int(workspace_id),
            "body": body,
            "kind": kind,
            "pinned": bool(pinned),
        },
        summary=f"Add note: {body[:80]}",
        workspace_id=int(workspace_id),
        chat_id=chat_id,
    )


async def propose_switch_workspace(
    workspace_id: int,
    chat_id: str = DEFAULT_CHAT,
) -> dict[str, Any]:
    return await create_proposal(
        tool="planning.switch_workspace",
        args={"workspace_id": int(workspace_id), "chat_id": chat_id},
        summary=f"Set active workspace to #{workspace_id}",
        workspace_id=int(workspace_id),
        chat_id=chat_id,
    )


# ── Executors (the actual DB writes, only ever reached post-approval) ──


async def _exec_create_workspace(session: Any, args: dict[str, Any]) -> int:
    result = await session.execute(
        text(
            "INSERT INTO planning_workspaces (name, intake_year, target_degree, owner, status)"
            " VALUES (:n, :y, :d, :o, :s) RETURNING id"
        ),
        args,
    )
    return result.scalar_one()


async def _exec_add_goal(session: Any, args: dict[str, Any]) -> int:
    result = await session.execute(
        text(
            "INSERT INTO planning_goals (workspace_id, title, description,"
            " parent_id, priority, status)"
            " VALUES (:workspace_id, :title, :description, :parent_id, :priority, 'open')"
            " RETURNING id"
        ),
        args,
    )
    return result.scalar_one()


async def _exec_add_task(session: Any, args: dict[str, Any]) -> int:
    params = dict(args)
    due = params.get("due_date")
    params["due_date"] = due[:10] if due else None
    result = await session.execute(
        text(
            "INSERT INTO planning_tasks (workspace_id, goal_id, title,"
            " description, due_date, status)"
            " VALUES (:workspace_id, :goal_id, :title, :description, :due_date, :status)"
            " RETURNING id"
        ),
        params,
    )
    return result.scalar_one()


async def _exec_record_decision(session: Any, args: dict[str, Any]) -> int:
    params = dict(args)
    if isinstance(params.get("evidence"), dict):
        params["evidence"] = json.dumps(params["evidence"])
    result = await session.execute(
        text(
            "INSERT INTO planning_decisions (workspace_id, title, rationale, evidence, status)"
            " VALUES (:workspace_id, :title, :rationale, :evidence, :status)"
            " RETURNING id"
        ),
        params,
    )
    return result.scalar_one()


async def _exec_supersede_decision(session: Any, args: dict[str, Any]) -> int:
    decision_id = int(args["decision_id"])
    old = await session.execute(
        text("SELECT workspace_id FROM planning_decisions WHERE id = :id"),
        {"id": decision_id},
    )
    old_row = old.one_or_none()
    if old_row is None:
        raise ValueError(f"Decision {decision_id} not found")
    workspace_id = old_row.workspace_id

    new = await session.execute(
        text(
            "INSERT INTO planning_decisions (workspace_id, title, rationale, evidence, status)"
            " VALUES (:w, :t, :r, :e, 'confirmed') RETURNING id"
        ),
        {
            "w": workspace_id,
            "t": args["title"],
            "r": args.get("rationale"),
            "e": json.dumps({"supersedes": decision_id, "superseded_at": _now_iso()}),
        },
    )
    new_id = new.scalar_one()
    await session.execute(
        text(
            "UPDATE planning_decisions SET status = 'superseded', superseded_by_id = :new_id"
            " WHERE id = :id"
        ),
        {"new_id": new_id, "id": decision_id},
    )
    return new_id


async def _exec_update_task_status(session: Any, args: dict[str, Any]) -> None:
    await session.execute(
        text("UPDATE planning_tasks SET status = :status, updated_at = now() WHERE id = :task_id"),
        {"task_id": int(args["task_id"]), "status": args["status"]},
    )


async def _exec_add_note(session: Any, args: dict[str, Any]) -> int:
    result = await session.execute(
        text(
            "INSERT INTO planning_notes (workspace_id, kind, body, pinned)"
            " VALUES (:workspace_id, :kind, :body, :pinned) RETURNING id"
        ),
        args,
    )
    return result.scalar_one()


async def _exec_switch_workspace(session: Any, args: dict[str, Any]) -> None:
    await session.execute(
        text(
            "INSERT INTO planning_state (chat_id, active_workspace_id, last_active_at)"
            " VALUES (:chat_id, :workspace_id, now())"
            " ON CONFLICT (chat_id) DO UPDATE"
            " SET active_workspace_id = :workspace_id, last_active_at = now()"
        ),
        args,
    )


_EXECUTORS: dict[str, Any] = {
    "planning.create_workspace": _exec_create_workspace,
    "planning.add_goal": _exec_add_goal,
    "planning.add_task": _exec_add_task,
    "planning.record_decision": _exec_record_decision,
    "planning.supersede_decision": _exec_supersede_decision,
    "planning.update_task_status": _exec_update_task_status,
    "planning.add_note": _exec_add_note,
    "planning.switch_workspace": _exec_switch_workspace,
}
