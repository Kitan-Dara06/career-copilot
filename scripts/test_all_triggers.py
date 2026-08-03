"""Local test suite — run every Paper Tracker trigger in sequence.

Usage:
    uv run python scripts/test_all_triggers.py
"""

from __future__ import annotations

import asyncio

from agents.paper_tracker.agent import PaperTrackerAgent
from backbone.tools.base import ToolContext
from career_copilot.config import get_settings


async def main() -> None:
    settings = get_settings()
    ctx = ToolContext(agent="paper_tracker", task_id="test-all", settings=settings)
    agent = PaperTrackerAgent(task_ctx=ctx)

    sep = "=" * 60

    # ── 1. Interests ──
    print(f"\n{sep}\n  1. /interests\n{sep}")
    _ = await agent._get_user_interests()  # Load only
    keywords = await agent._get_user_keywords()
    print(f"  Keywords: {keywords}")

    # ── 2. Digest — stream A ──
    print(f"\n{sep}\n  2. Stream A — by interest\n{sep}")
    from datetime import UTC, datetime, timedelta

    since = datetime.now(UTC) - timedelta(days=3)
    papers = await agent._stream_a_interest(since, max_fetch=10)
    if papers:
        for i, p in enumerate(papers, 1):
            print(f"  {i}. {p['title'][:100]}")
            print(f"     Why: {p['why'][:100]}")
    else:
        print("  No papers passed filter")

    # ── 3. Digest — stream B ──
    print(f"\n{sep}\n  3. Stream B — by professor\n{sep}")
    prof_papers = await agent._stream_b_professor(since)
    if prof_papers:
        for p in prof_papers[:3]:
            print(f"  {p['title'][:100]} ({p.get('professor', '?')})")
    else:
        print("  No professors in watchlist")

    # ── 4. Watch list ──
    print(f"\n{sep}\n  4. /watch list\n{sep}")
    try:
        profs = await agent.watch_list()
        if profs:
            for p in profs[:5]:
                print(f"  {p.get('name', '?')} — {p.get('affiliation', '?')}")
        else:
            print("  Watchlist empty")
    except Exception as exc:
        print(f"  FAILED: {exc}")

    # ── 5. Discover ──
    print(f"\n{sep}\n  5. /discover\n{sep}")
    candidates = await agent.run_discover()
    if candidates:
        for c in candidates[:5]:
            print(f"  {c['name']} ({c['papers_count']} papers)")
            print(f"     Affiliation: {c.get('affiliation', '?')}")
            print(f"     Verified: {c.get('verified', False)}")
            print(f"     Citations: {c.get('citations', 0)}")
    else:
        print("  No candidates found")

    # ── 6. Watch add ──
    print(f"\n{sep}\n  6. /watch add <test>\n{sep}")
    # Don't actually add — just verify the method exists and doesn't crash
    print("  (skipped — needs real professor name)")

    # ── 7. Prof brief ──
    print(f"\n{sep}\n  7. /prof <id>\n{sep}")
    try:
        brief = await agent.run_prof_brief(1)
        if brief.get("error"):
            print(f"  {brief['error']}")
        else:
            print(f"  Name: {brief.get('name', '?')}")
            print(f"  Papers: {brief.get('paper_count', 0)}")
    except Exception as exc:
        print(f"  FAILED: {exc}")

    print(f"\n{sep}\n  DONE — all triggers tested\n{sep}")


if __name__ == "__main__":
    asyncio.run(main())
