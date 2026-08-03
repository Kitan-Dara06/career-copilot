#!/usr/bin/env python3
"""Live test of all Job Hunter endpoints — debug mode skips embeddings + LLM.

Usage: uv run python scripts/test_jh_endpoints.py [region] [--debug]
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime

from backbone.tools.base import ToolContext
from career_copilot.config import get_settings


def _make_ctx() -> ToolContext:
    return ToolContext(agent="job_hunter", task_id=f"test_{datetime.now(UTC).timestamp():.0f}", settings=get_settings())


async def test_companies(agent, region):
    print("\n── 1. /companies ──")
    companies = agent._load_watchlist(region)
    print(f"   Watchlist size: {len(companies)}")
    by_tier: dict[int, int] = {}
    for c in companies[:5]:
        print(f"   - {c['name']} [{c.get('region')}] tier={c.get('source_tier')}")
        tier = c.get("source_tier", 0)
        by_tier[tier] = by_tier.get(tier, 0) + 1
    print(f"   Tier breakdown: {by_tier}")
    print("   ✅ /companies works")


async def test_prefs(agent):
    print("\n── 2. /prefs ──")
    profile = agent._load_career_profile()
    regions = profile.get("target_regions", {})
    salary_floors = profile.get("salary_floor", {})
    print(f"   Primary region: {regions.get('primary')}")
    print(f"   Secondary: {regions.get('secondary')}")
    print(f"   Future: {regions.get('future_relocation')}")
    print(f"   Role types: {profile.get('target_role_types')}")
    print(f"   Min match score: {profile.get('min_match_score', 'default')}")
    print(f"   Digest: every {profile.get('digest_frequency_days')} days at {profile.get('digest_time')}")
    for r, floor in (salary_floors or {}).items():
        cur = profile.get("salary_currency", {}).get(r, "N/A")
        period = profile.get("salary_period", {}).get(r, "N/A")
        print(f"   Salary floor: {r} = {floor} {cur}/{period}")
    print("   ✅ /prefs works")


async def test_prefs_set(agent):
    print("\n── 3. /prefs set ──")
    tests = [
        ("match.score", "0.60", "0.55"),
        ("salary.canada", "110000", "100000"),
        ("digest.cadence", "4", "3"),
        ("digest.time", "09:30", "08:00"),
        ("bogus.key", "x", None),  # should fail
    ]
    for key, new_val, restore in tests:
        ok, msg = agent.set_preference(key, new_val)
        icon = "✅" if (ok or restore is None) else "❌"
        print(f"   {icon} set {key} {new_val}: {msg[:120]}")
        if restore is not None:
            agent.set_preference(key, restore)
    print("   ✅ /prefs set works")


async def test_jobs_debug(agent, region):
    """Test /jobs without embeddings or LLM enrichment (fast path)."""
    from agents.job_hunter.agent import JobHunterAgent
    print(f"\n── 4. /jobs{f' {region}' if region else ''} (debug mode) ──")
    t0 = time.monotonic()

    # Step 1: Fetch
    companies = agent._load_watchlist(region)
    profile = agent._load_career_profile()
    print(f"   Fetching {len(companies)} companies...")
    all_postings: list[dict] = []
    for company in companies:
        src = company.get("source_tier")
        name = company["name"]
        region_tag = company.get("region", "")
        try:
            if src == 1:
                postings = await agent._fetch_ats(company)
            elif src == 2:
                postings = await agent._fetch_careers(company)
            elif src == 3:
                postings = await agent._fetch_tavily(company)
            else:
                continue
            for p in postings:
                p["_region"] = region_tag
                p["_organization"] = name
            all_postings.extend(postings)
        except Exception as exc:
            print(f"   ⚠️ {name} failed: {exc}")
            continue

    fetch_time = time.monotonic() - t0
    print(f"   Fetched {len(all_postings)} raw postings in {fetch_time:.1f}s")

    if not all_postings:
        print("   ✅ /jobs works (no postings found)")
        return

    # Step 2: Score (skip embeddings — use random scores for speed)
    print("   Skipping embeddings in debug mode — assigning mock scores...")
    import random
    for p in all_postings:
        p["_score_raw"] = round(random.uniform(0.3, 0.9), 2)

    min_match = profile.get("min_match_score", 0.55)
    scored = [s for s in all_postings if s["_score_raw"] >= min_match]
    scored.sort(key=lambda s: s["_score_raw"], reverse=True)

    max_per = profile.get("max_results_per_digest", 20)
    top = scored[:max_per]

    # Step 3: Annotate
    for item in top:
        item["_salary"] = agent._annotate_salary(item, profile)
        item["_visa"] = agent._annotate_visa(item, profile)

    total_time = time.monotonic() - t0
    print(f"   Results: {len(top)} above threshold ({len(scored)} total, {len(all_postings)} raw)")
    for i, r in enumerate(top[:10]):
        title = r.get("title", "?")[:80]
        org = r.get("_organization", "?")
        score = r.get("_score_raw", 0)
        salary = r.get("_salary", "")
        visa = r.get("_visa", "")
        role = r.get("role_type", "?")
        region_tag = r.get("_region", "")
        print(f"   [{i+1}] {org} | {title}")
        print(f"       score={score:.2f} role={role} region={region_tag} salary={salary} visa={visa}")
    print(f"   ✅ /jobs works in {total_time:.1f}s ({len(top)} results)")


async def test_jobs_live(agent, region):
    """Full /jobs with embeddings + LLM enrichment (slow path)."""
    print(f"\n── 4. /jobs{f' {region}' if region else ''} (live mode) ──")
    t0 = time.monotonic()
    try:
        results = await agent.run_discovery(region=region)
        elapsed = time.monotonic() - t0
        print(f"   Raw postings above threshold: {len(results)} in {elapsed:.1f}s")
        for i, r in enumerate(results[:10]):
            title = r.get("title", "?")[:80]
            org = r.get("_organization", "?")
            score = r.get("_score_raw", 0)
            salary = r.get("_salary", "")
            visa = r.get("_visa", "")
            role = r.get("role_type", "?")
            region_tag = r.get("_region", "")
            print(f"   [{i+1}] {org} | {title}")
            print(f"       score={score:.2f} role={role} region={region_tag} salary={salary} visa={visa}")
        print(f"   ✅ /jobs works ({len(results)} results in {elapsed:.1f}s)")
    except Exception as exc:
        print(f"   ❌ /jobs FAILED: {exc}")
        import traceback
        traceback.print_exc()


async def test_saved(agent):
    print("\n── 5. /saved ──")
    try:
        saved = await agent.get_saved_postings()
        print(f"   Saved postings: {len(saved)}")
        for s in saved[:5]:
            print(f"   - {s.get('organization', '?')}: {s.get('title', '?')[:80]}")
        print("   ✅ /saved works")
    except Exception as exc:
        print(f"   ❌ /saved FAILED: {exc}")


async def main(region: str | None = None, debug: bool = False):
    from agents.job_hunter.agent import JobHunterAgent

    print("=" * 60)
    print(f"JOB HUNTER — Endpoint Tests ({'debug' if debug else 'live'} mode)")
    print("=" * 60)

    agent = JobHunterAgent(task_ctx=_make_ctx())

    await test_companies(agent, region)
    await test_prefs(agent)
    await test_prefs_set(agent)

    if debug:
        await test_jobs_debug(agent, region)
    else:
        await test_jobs_live(agent, region)

    await test_saved(agent)

    print("\n" + "=" * 60)
    print("All endpoint tests complete.")
    print("=" * 60)


if __name__ == "__main__":
    region = None
    debug = False
    for arg in sys.argv[1:]:
        if arg == "--debug":
            debug = True
        elif arg == "--live":
            debug = False
        else:
            region = arg
    asyncio.run(main(region, debug))
