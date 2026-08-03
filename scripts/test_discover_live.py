"""Live smoke test for /discover.

Runs the full discover flow (S2 → embed → cluster → dedup → Tavily + Firecrawl +
verify LLM → region balance) and prints every candidate so you can eyeball the
region mix, the position label, and the country the LLM extracted.

Usage:
    uv run python scripts/test_discover_live.py
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.paper_tracker.agent import PaperTrackerAgent  # noqa: E402
from backbone.tools.base import ToolContext  # noqa: E402
from career_copilot.config import get_settings  # noqa: E402


async def main() -> None:
    settings = get_settings()
    ctx = ToolContext(
        agent="paper_tracker", task_id="local-discover-test", settings=settings
    )
    agent = PaperTrackerAgent(task_ctx=ctx)

    print(f"[disco] start {datetime.now(UTC).isoformat()}")
    print(f"[disco] regions: {agent._discover_regions()}")
    try:
        cands = await agent.run_discover()
    except Exception:
        print("[disco] run_discover crashed:")
        traceback.print_exc()
        return

    print(f"\n[disco] {len(cands)} candidates:")
    for i, c in enumerate(cands[:10], 1):
        print(f"\n  {i}. {c.get('name')}")
        print(f"     position      = {c.get('position', '')!r}")
        print(f"     university    = {c.get('university', '')!r}")
        print(f"     department    = {c.get('department', '')!r}")
        print(f"     affiliation   = {c.get('affiliation', '')!r}")
        print(f"     research_area = {c.get('research_area', '')!r}")
        print(f"     country/region = {c.get('country')!r} / {c.get('region')!r}")
        print(f"     citations h  = {c.get('citations')} h={c.get('h_index')}")
        print(f"     sim/score    = {c.get('similarity')} comb={c.get('combined_score')}")
        print(f"     focus         = {c.get('focus', '')!r}")
        print(f"     verify_source = {c.get('verify_source', '')!r}")
        print(f"     author_in_top = {c.get('author_in_top_paper', '')!r}")
        print(f"     homepage      = {c.get('homepage', '')!r}")
        print(f"     co_workers    = {c.get('co_workers', [])!r}")

    # Region tally so we can verify the mix.
    from collections import Counter

    tally = Counter(c.get("region") for c in cands)
    print(f"\n[disco] region tally: {dict(tally)}")
    print(f"[disco] end   {datetime.now(UTC).isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())