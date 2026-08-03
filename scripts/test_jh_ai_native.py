#!/usr/bin/env python3
"""Run the live Job Hunter discovery pipeline on the 5 newly-added AI-native
companies, to confirm the per-cluster weighted-max scorer actually surfaces
real AI/agent/LLM postings above the 0.45 threshold.

Skips the larger watchlist to keep the fetch fast (~25s for Anthropic+Together
AI+DeepMind+Vercel+Databricks instead of 70s+ for the full 86-company list).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from backbone.tools.base import ToolContext
from career_copilot.config import get_settings


def _make_ctx() -> ToolContext:
    return ToolContext(agent="job_hunter", task_id=f"test_{datetime.now(UTC).timestamp():.0f}", settings=get_settings())


SYNTHETIC_WATCHLIST = [
    {"name": "Anthropic",   "region": "international_remote", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "anthropic"},
    {"name": "Together AI", "region": "international_remote", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "togetherai"},
    {"name": "Vercel",      "region": "international_remote", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "vercel"},
    {"name": "DeepMind",    "region": "international_remote", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "deepmind"},
    {"name": "Stability AI","region": "eu",                    "source_tier": 1, "ats": "greenhouse", "ats_company_id": "stabilityai"},
]
# Aaliyah doesn't qualify for Databricks' Senior/Staff roles in this test scope,
# so we skip Databricks to keep the run under 60s. The production digester is
# batched+concurrency-limited and can handle the full 800-posting Databricks feed.

import re
AI_TITLE_RE = re.compile(
    r"(intern|new grad|co-?op|research.*engineer|research.*scientist|"
    r"ml engineer|ai engineer|llm|agent|rag|retrieval|nlp|genai)",
    re.IGNORECASE,
)


async def main():
    from agents.job_hunter.agent import JobHunterAgent

    agent = JobHunterAgent(task_ctx=_make_ctx())
    profile = agent._load_career_profile()
    skills = agent._load_skill_clusters()
    await agent._ensure_user_skill_vec(skills)

    min_match = profile.get("min_match_score", 0.45)
    print("=" * 80)
    print(f"JOB HUNTER — live scoring on AI-native companies")
    print(f"Threshold: {min_match}  ·  gray band: 0.32-0.52  ·  scoring: per-cluster weighted max")
    print("=" * 80)

    all_postings: list[dict] = []
    for company in SYNTHETIC_WATCHLIST:
        try:
            postings = await agent._fetch_ats(company)
            for p in postings:
                p["_region"] = company["region"]
                p["_organization"] = company["name"]
            # Pre-filter to AI-relevant titles only — for this test we don't
            # need to score 413 Anthropic postings when only the AI ones matter.
            # _fetch_ats returns dicts (model_dump()), so use ['title'] access.
            filtered = [p for p in postings if AI_TITLE_RE.search(p.get("title", ""))]
            all_postings.extend(filtered)
            print(f"  fetched {len(postings):>4} from {company['name']} -> {len(filtered)} AI-relevant")
        except Exception as exc:
            print(f"  ! fetch failed {company['name']}: {exc}")
            continue

    print(f"\nTotal raw postings: {len(all_postings)}")
    print("\nScoring + gray-band LLM judge...")
    scored = await agent._score_all(all_postings, skills, profile)
    passed = [s for s in scored if s["_score_raw"] >= min_match]
    passed.sort(key=lambda s: s["_score_raw"], reverse=True)
    print(f"Passed threshold ({min_match}): {len(passed)} of {len(scored)}\n")

    max_per = profile.get("max_results_per_digest", 20)
    # For the test we cap at 15 so we see all PASSES clearly.
    top = passed[:15]
    print(f"Top {len(top)} (max_results_per_digest = {max_per}):\n")
    for i, r in enumerate(top):
        title = r.get("title", "?")[:70]
        org = r.get("_organization", "?")
        score = r.get("_score_raw", 0)
        cluster = r.get("_top_cluster", "?")
        salary = agent._annotate_salary(r, profile)
        print(f"  [{i+1}] {org:<14} | score={score:.2f}  cluster={cluster:<20}  {title}")
        if salary:
            print(f"      salary: {salary}")

    if not top:
        # Print top 10 by score anyway so we can see why threshold misses
        scored.sort(key=lambda s: s["_score_raw"], reverse=True)
        print("\nNo postings above threshold. Top 10 by score:")
        for i, r in enumerate(scored[:10]):
            title = r.get("title", "?")[:70]
            org = r.get("_organization", "?")
            score = r.get("_score_raw", 0)
            cluster = r.get("_top_cluster", "?")
            print(f"  [{i+1}] {org:<14} | score={score:.2f}  cluster={cluster:<20}  {title}")


if __name__ == "__main__":
    asyncio.run(main())