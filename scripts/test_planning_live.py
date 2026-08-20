"""Live integration test for the Phase 2 planning read side.

Exercises the MCP server against a real (or seeded) Supabase so you can see
exactly what a Telegram user would receive when they ask Hermes to query the
planning workspace. Runs in-process — no Telegram, no Hermes, no broker.

Usage:
    uv run python -m scripts.test_planning_live

Optional: ``--seed`` to write a "Master's 2027" workspace with draft goals/
tasks/decisions if the DB is empty, so the read tools return something.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from backbone.db.session import async_session_factory
from backbone.mcp.server import mcp


def _hr(title: str) -> None:
    print(f"\n{'─' * 60}\n{title}\n{'─' * 60}")


async def _ensure_seed() -> int:
    """Seed a "Master's 2027" workspace + draft goals/tasks/decisions.

    Returns the new workspace id, or 0 if one already exists.
    """
    async with async_session_factory()() as session:
        existing = await session.execute(
            text(
                "SELECT id FROM planning_workspaces"
                " WHERE owner = 'aaliyah' AND name = 'Master''s 2027'"
            )
        )
        row = existing.one_or_none()
        if row is not None:
            print(f"  workspace 'Master''s 2027' already exists (id={row.id})")
            return 0

        w = await session.execute(
            text(
                "INSERT INTO planning_workspaces"
                " (name, intake_year, target_degree, owner, status)"
                " VALUES ('Master''s 2027', 2027, 'MSc', 'aaliyah', 'active')"
                " RETURNING id"
            )
        )
        ws_id = w.scalar_one()

        # Draft goals
        goal_specs = [
            ("Research direction", 1, "Identify target subfields and 2-3 short-list professors each."),
            ("Funding strategy", 2, "Decide GRE, TOEFL, scholarship track by end of Q1."),
            ("Documents", 3, "SoP, CV, transcripts, 3 LoRs - draft then polish."),
            ("Professor outreach", 4, "Shortlist 6-8 professors; send 2 cold emails per week."),
        ]
        goal_ids: list[int] = []
        for title, priority, desc in goal_specs:
            r = await session.execute(
                text(
                    "INSERT INTO planning_goals"
                    " (workspace_id, title, description, priority, status)"
                    " VALUES (:w, :t, :d, :p, 'open')"
                    " RETURNING id"
                ),
                {"w": ws_id, "t": title, "d": desc, "p": priority},
            )
            goal_ids.append(r.scalar_one())

        # Tasks: mix of overdue, open, done
        now = datetime.now(UTC).date()
        task_specs = [
            (goal_ids[0], "Watch 3 NIPS talks on RAG evaluation", now - timedelta(days=10), "todo"),
            (goal_ids[1], "Decide on GRE (skip or take)", now + timedelta(days=14), "todo"),
            (goal_ids[2], "Draft SoP v0.1", now + timedelta(days=30), "todo"),
            (goal_ids[3], "Identify 6 shortlist professors", now + timedelta(days=7), "doing"),
            (goal_ids[0], "Skim recent evaluation-infra papers", now, "done"),
        ]
        for goal_id, title, due, status in task_specs:
            await session.execute(
                text(
                    "INSERT INTO planning_tasks"
                    " (goal_id, workspace_id, title, due_date, status)"
                    " VALUES (:g, :w, :t, :d, :s)"
                ),
                {"g": goal_id, "w": ws_id, "t": title, "d": due, "s": status},
            )

        # Decisions
        await session.execute(
            text(
                "INSERT INTO planning_decisions"
                " (workspace_id, title, rationale, status, evidence)"
                " VALUES (:w, 'Apply primarily to Canada', 'Profile fit, GPA, research alignment', 'confirmed', :ev)"
            ),
            {
                "w": ws_id,
                "ev": json.dumps({
                    "sources": ["data/user_profile.yaml", "data/user_skills.yaml"],
                    "retrieved_at": datetime.now(UTC).isoformat(),
                }),
            },
        )
        await session.execute(
            text(
                "INSERT INTO planning_decisions"
                " (workspace_id, title, rationale, status)"
                " VALUES (:w, 'Skip GRE this year', 'Cost, time, several target programs do not require it', 'proposed')"
            ),
            {"w": ws_id},
        )

        # Notes
        await session.execute(
            text(
                "INSERT INTO planning_notes (workspace_id, kind, body, pinned)"
                " VALUES (:w, 'observation', 'Pinned: focus on RAG evaluation + agent memory.', true)"
            ),
            {"w": ws_id},
        )

        # State - mark this as the active workspace for chat 'aaliyah'
        await session.execute(
            text(
                "INSERT INTO planning_state (chat_id, active_workspace_id)"
                " VALUES ('aaliyah', :w)"
                " ON CONFLICT (chat_id) DO UPDATE"
                " SET active_workspace_id = :w, last_active_at = now()"
            ),
            {"w": ws_id},
        )

        await session.commit()
        print(f"  seeded workspace 'Master''s 2027' (id={ws_id}) with {len(goal_specs)} goals, {len(task_specs)} tasks, 2 decisions, 1 note")
        return ws_id


async def _call(tool: str, args: dict[str, Any] | None = None) -> Any:
    """Invoke a registered MCP tool in-process and return its raw result.

    Uses the underlying tool manager with ``convert_result=False`` so the real
    Python return value (dict or list) comes back untouched — the public
    ``FastMCP.call_tool`` instead splits collections into one TextContent block
    per element, which loses list-ness for ``list_*`` tools.
    """
    return await mcp._tool_manager.call_tool(tool, args or {})


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true", help="seed Master's 2027 workspace if missing")
    args = parser.parse_args()

    if args.seed:
        _hr("0. Seeding")
        await _ensure_seed()

    # Find the active workspace
    active = await _call("career.planning.get_summary", {"chat_id": "aaliyah"})
    if "error" in active:
        print(f"\n[!] No active workspace: {active['error']}")
        print("    Re-run with --seed to create one.\n")
        sys.exit(1)
    wid = active["workspace"]["id"]
    print(f"\nActive workspace: id={wid}, name={active['workspace']['name']}")
    print(f"Provenance version_key: {active['provenance']['version_key']}")

    _hr("1. career.planning.get_summary (the session-bootstrap payload)")
    print(json.dumps(active, indent=2, default=str)[:1200])

    _hr("2. career.planning.list_goals")
    goals = await _call("career.planning.list_goals", {"workspace_id": wid})
    print(json.dumps(goals, indent=2, default=str)[:800])

    _hr("3. career.planning.list_tasks (status=todo, due_before=today)")
    today = datetime.now(UTC).date().isoformat()
    tasks = await _call("career.planning.list_tasks", {"workspace_id": wid, "due_before": today})
    print(json.dumps(tasks, indent=2, default=str)[:800])

    _hr("4. career.planning.list_decisions")
    decisions = await _call("career.planning.list_decisions", {"workspace_id": wid})
    print(json.dumps(decisions, indent=2, default=str)[:800])

    _hr("5. career.planning.list_notes")
    notes = await _call("career.planning.list_notes", {"workspace_id": wid})
    print(json.dumps(notes, indent=2, default=str)[:500])

    _hr("6. /ask a user query (simulated via raw tool calls)")
    print("Imagine the user asks: 'what am I behind on?'")
    print()
    overdue = [t for t in tasks if t["status"] in ("todo", "doing") and t["due_date"] and t["due_date"] < today]
    print(f"  -> overdue tasks ({len(overdue)}):")
    for t in overdue:
        print(f"     * {t['title']} (due {t['due_date']})")

    print()
    print("Imagine the user asks: 'what did we decide about GRE?'")
    gre = [d for d in decisions if "GRE" in d["title"]]
    print(f"  -> matching decisions ({len(gre)}):")
    for d in gre:
        print(f"     * [{d['status']}] {d['title']} — {d['rationale']}")

    print(f"\nAll {len(active.get('provenance', {}).get('sources', []))} sources tracked in provenance.")


if __name__ == "__main__":
    asyncio.run(main())