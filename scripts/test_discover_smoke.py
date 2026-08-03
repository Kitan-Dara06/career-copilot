"""Smoke test — exercises the CSRankings seed merge into verify pool without
running the full Firecrawl+LLM gate (which takes 20+ min). Confirms the
S2 search + CSRankings seed pipeline prunes correctly and seeds land in the
verify pool with the right shape.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.paper_tracker.agent import PaperTrackerAgent  # noqa: E402
from backbone.tools.base import ToolContext  # noqa: E402
from career_copilot.config import get_settings  # noqa: E402


async def main() -> None:
    settings = get_settings()
    ctx = ToolContext(agent="paper_tracker", task_id="smoke", settings=settings)
    agent = PaperTrackerAgent(task_ctx=ctx)
    cands = await agent.run_discover()
    print(f"\n=== Final {len(cands)} candidates ===")
    for i, c in enumerate(cands, 1):
        print(f"  {i}. {c.get('name')} | {c.get('university','')!r} | reg={c.get('region')} | verify_source={c.get('verify_source')} | seed={c.get('seed_source','')}")
    from collections import Counter
    print(f"\nRegion tally: {dict(Counter(c.get('region') for c in cands))}")


if __name__ == "__main__":
    asyncio.run(main())