"""End-to-end save loop: /jobs canada → persist → jh_save → /saved.

Run a region-limited discovery (Canada, ~13 companies), pick the top-posting,
save it via the callback handler, then verify /saved returns it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backbone.dispatcher.dispatcher import Dispatcher  # noqa: E402
from backbone.dispatcher.wiring import wire_job_hunter, wire_paper_tracker  # noqa: E402
from backbone.dispatcher.task import TaskResult  # noqa: E402
from agents.job_hunter.agent import JobHunterAgent  # noqa: E402
from backbone.tools.base import ToolContext  # noqa: E402
from career_copilot.config import get_settings  # noqa: E402


async def main() -> None:
    settings = get_settings()
    ctx = ToolContext(agent="job_hunter", task_id="save-loop-test", settings=settings)
    agent = JobHunterAgent(task_ctx=ctx)

    print("1. /jobs canada (live API)")
    items = await agent.run_discovery(region="canada")
    ext_id = ""
    title = ""
    if items:
        top = items[0]
        ext_id = top.get("external_id", "")
        title = top.get("title", "")[:80]
        print(f"   Top: {title} (id={ext_id}) match={top.get('_score_raw','?')}")
    else:
        print("   No postings above 55%. Creating a manual test posting.")
        from backbone.db.session import async_session_factory
        from sqlalchemy import text
        fac = async_session_factory()
        async with fac() as s:
            await s.execute(text(
                "INSERT INTO job_hunter_openings (external_id, source, source_url, title, organization, description, role_type, region) "
                "VALUES ('test:loop:001', 'manual', 'https://test.com', 'ML Engineer Intern', 'Borealis AI', 'Build retrieval pipelines.', 'internship', 'canada') "
                "ON CONFLICT (external_id) DO NOTHING"
            ))
            await s.commit()
        ext_id = "test:loop:001"
        title = "ML Engineer Intern — Borealis AI"

    # Save via the callback handler
    print("\n2. Save via dispatcher callback")
    d = Dispatcher()
    wire_paper_tracker(d)
    wire_job_hunter(d)
    r: TaskResult = await d.handle_callback(
        {"command": "jh_save", "external_id": ext_id}
    )
    print(f"   jh_save result: {r.output} (success={r.success})")

    # Check /saved
    print("\n3. /saved")
    r = await d.handle_command("u1", "saved")
    print(f"   Output:\n{r.output}")


if __name__ == "__main__":
    asyncio.run(main())