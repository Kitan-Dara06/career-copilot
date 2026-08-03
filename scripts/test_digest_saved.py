"""Verify /digest on/off/at and /saved work with real DB and scheduler."""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backbone.dispatcher.dispatcher import Dispatcher  # noqa: E402
from backbone.dispatcher.wiring import wire_job_hunter, wire_paper_tracker  # noqa: E402
from backbone.dispatcher.task import Task  # noqa: E402


async def main() -> None:
    d = Dispatcher()
    wire_paper_tracker(d)
    wire_job_hunter(d)

    # ── /digest on ──
    print("1. /digest on")
    r = await d.handle_command("u1", "digest", ["on"])
    print(f"   output: {r.output}")

    # ── /digest at 14:30 ──
    print("2. /digest at 14:30")
    r = await d.handle_command("u1", "digest", ["at", "14:30"])
    print(f"   output: {r.output}")

    # ── /digest off ──
    print("3. /digest off")
    r = await d.handle_command("u1", "digest", ["off"])
    print(f"   output: {r.output}")

    # ── /digest now (just confirm it doesn't crash) ──
    print("4. /digest now")
    r = await d.handle_command("u1", "digest", ["now"])
    print(f"   output (first 120 chars): {str(r.output)[:120]}")

    # ── /saved ──
    print("5. /saved")
    r = await d.handle_command("u1", "saved")
    print(f"   output: {r.output}")

    # ── Mark a posting as saved manually then re-check /saved ──
    from agents.job_hunter.agent import JobHunterAgent  # noqa: E402
    from backbone.tools.base import ToolContext  # noqa: E402
    from career_copilot.config import get_settings  # noqa: E402

    ctx = ToolContext(agent="job_hunter", task_id="saved-test", settings=get_settings())
    a = JobHunterAgent(task_ctx=ctx)
    # Write a dummy posting first so there's something to save against.
    from sqlalchemy import text
    from backbone.db.session import async_session_factory
    fac = async_session_factory()
    async with fac() as s:
        await s.execute(text(
            "INSERT INTO job_hunter_openings (external_id, source, source_url, title, organization, description, role_type, region) "
            "VALUES (:ext, 'manual', 'https://test.com', 'ML Engineer Intern', 'Flutterwave', 'Build retrieval pipelines.', 'internship', 'nigeria') "
            "ON CONFLICT (external_id) DO NOTHING"
        ), {"ext": "test:demo-001"})
        await s.commit()
    await a.mark_saved("test:demo-001")

    print("6. /saved (after saving test:demo-001)")
    r = await d.handle_command("u1", "saved")
    print(f"   output: {r.output}")


if __name__ == "__main__":
    asyncio.run(main())