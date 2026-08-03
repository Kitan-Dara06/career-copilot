"""Live smoke test for every Job Hunter command — real API calls.

Usage:    uv run python scripts/test_jh_live.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.job_hunter.agent import JobHunterAgent  # noqa: E402
from backbone.tools.base import ToolContext  # noqa: E402
from career_copilot.config import get_settings  # noqa: E402


async def main() -> None:
    settings = get_settings()
    ctx = ToolContext(agent="job_hunter", task_id="live-test", settings=settings)
    agent = JobHunterAgent(task_ctx=ctx)

    sep = "=" * 70

    # ── 1. /help_jh ──
    print(f"\n{sep}\n  1. /help_jh\n{sep}")
    print(
        "Job Hunter\n\n"
        "/jobs [region]          Run discovery\n"
        "/jobs nigeria           Nigeria only\n"
        "/jobs canada            Canada only\n"
        "/companies [region]     List watchlist companies\n"
        "/saved                   View saved postings\n"
        "/prefs                   Show career preferences\n"
        "/help_jh                Show this message"
    )

    # ── 2. /companies ──
    print(f"\n{sep}\n  2. /companies (all regions, first 15)\n{sep}")
    all_companies = agent._load_watchlist()
    from collections import Counter
    tally = Counter(c.get("region") for c in all_companies)
    print(f"  Total: {len(all_companies)} companies")
    print(f"  By region: {dict(tally)}")
    print(f"  By tier: {dict(Counter(c.get('source_tier') for c in all_companies))}")
    print("\n  First 15:")
    for c in all_companies[:15]:
        print(f"    {c['name']:40s} [{c.get('region')}]  tier={c.get('source_tier')}")

    # ── 3. /prefs ──
    print(f"\n{sep}\n  3. /prefs\n{sep}")
    p = agent._load_career_profile()
    user = p.get("user", {})
    print(f"  Name: {user.get('name')}")
    print(f"  Education: {user.get('education_status')}, completion {user.get('degree_completion')}")
    print(f"  Target regions: primary={p.get('target_regions',{}).get('primary')}  secondary={p.get('target_regions',{}).get('secondary')}  future={p.get('target_regions',{}).get('future_relocation')}")
    print(f"  Role types: {p.get('target_role_types')}")
    print(f"  Visa: {p.get('visa_requirement')}")
    print(f"  Salary floor: {p.get('salary_floor')}")
    print(f"  Salary filter: {p.get('salary_filter_mode')}")
    print(f"  Digest cadence: every {p.get('digest_frequency_days')} days at {p.get('digest_time')}")

    # ── 4. /companies canada ──
    print(f"\n{sep}\n  4. /companies canada\n{sep}")
    ca = [c for c in all_companies if c.get("region") == "canada"]
    for c in ca:
        print(f"    {c['name']:35s}  tier={c.get('source_tier')}  ats={c.get('ats','-')}")

    # ── 5. /jobs canada (live API — ATS + Firecrawl + Tavily for Canada region only) ──
    print(f"\n{sep}\n  5. /jobs canada (live — hitting ATS + Firecrawl + Tavily)\n{sep}")
    items = await agent.run_discovery(region="canada")
    if not items:
        print("  No postings found for Canada region.")
    else:
        for i, it in enumerate(items, 1):
            ext = it.get("external_id", "?")
            title = it.get("title", "")[:120]
            org = it.get("_organization", it.get("organization", "?"))
            score = it.get("_score_raw", "?")
            salary = it.get("_salary", "")
            visa = it.get("_visa", "")
            src = it.get("source", "?")
            print(f"\n  {i}. {title}")
            print(f"     org={org} | source={src} | match={int(score*100) if isinstance(score,float) else score}%")
            if salary:
                print(f"     {salary}")
            if visa:
                print(f"     {visa}")
            print(f"     external_id={ext}")

    print(f"\n{sep}\n  Done.\n{sep}")


if __name__ == "__main__":
    asyncio.run(main())