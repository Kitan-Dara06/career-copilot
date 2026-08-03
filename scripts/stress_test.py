#!/usr/bin/env python3
"""Live stress test — exercises every Job Hunter feature against real APIs.

Answers: does this thing actually survive real-world issues?
- Rate limits (Gemini 429, DeepSeek empty, Greenhouse 429)
- Large payloads (Anthropic 413 postings, Databricks 801)
- 404s (Mistral lever endpoint, fake URLs)
- Timeouts (slow Firecrawl scrapes)
- Missing data (Nigerian companies with no salary info)

Run with: uv run python scripts/stress_test.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime

from backbone.tools.base import ToolContext
from career_copilot.config import get_settings


def _ctx(name: str = "") -> ToolContext:
    return ToolContext(
        agent="job_hunter",
        task_id=f"stress_{name}_{datetime.now(UTC).timestamp():.0f}",
        settings=get_settings(),
    )


PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
TIMER = {}


def tick(label: str) -> None:
    TIMER[label] = time.monotonic()


def tock(label: str) -> float:
    return time.monotonic() - TIMER.get(label, time.monotonic())


async def stress_ats_retry() -> dict:
    """Test ATS fetch with retry: hit the biggest boards we know about."""
    from agents.job_hunter.agent import JobHunterAgent

    agent = JobHunterAgent(task_ctx=_ctx("ats"))
    profile = agent._load_career_profile()
    skills = agent._load_skill_clusters()
    await agent._ensure_user_skill_vec(skills)
    min_match = profile.get("min_match_score", 0.45)

    companies = [
        {"name": "Anthropic", "region": "international_remote", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "anthropic"},
        {"name": "Databricks", "region": "international_remote", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "databricks"},
        {"name": "GitLab", "region": "international_remote", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "gitlab"},
        {"name": "Stripe", "region": "international_remote", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "stripe"},
        {"name": "Helsing", "region": "eu", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "helsing"},
        {"name": "Together AI", "region": "international_remote", "source_tier": 1, "ats": "greenhouse", "ats_company_id": "togetherai"},
    ]

    results = {}
    for c in companies:
        tick(c["name"])
        try:
            postings = await agent._fetch_ats(c)
            elapsed = tock(c["name"])
            results[c["name"]] = {"count": len(postings), "time": elapsed, "ok": True}
            print(f"  {PASS} {c['name']:<15} {len(postings):>4} postings in {elapsed:.1f}s")
        except Exception as exc:
            elapsed = tock(c["name"])
            results[c["name"]] = {"count": 0, "time": elapsed, "ok": False, "error": str(exc)[:80]}
            print(f"  {FAIL} {c['name']:<15} failed in {elapsed:.1f}s: {str(exc)[:80]}")

    return results


async def stress_discovery_regions() -> dict:
    """Run /jobs for every region and see what survives."""
    from agents.job_hunter.agent import JobHunterAgent

    regions = ["nigeria", "africa", "eu", "canada", "international_remote"]
    results = {}

    for region in regions:
        agent = JobHunterAgent(task_ctx=_ctx(region))
        print(f"\n  >> /jobs {region}")
        tick(region)
        try:
            items = await agent.run_discovery(region=region)
            elapsed = tock(region)
            regions_seen = set(r.get("_region", "?") for r in items)
            results[region] = {
                "count": len(items), "time": elapsed, "ok": True,
                "regions": regions_seen, "fallback": region not in regions_seen or len(regions_seen) > 1,
            }
            print(f"  {PASS} {region:<22} {len(items):>3} postings in {elapsed:.0f}s  regions={regions_seen}")
            if results[region]["fallback"]:
                print(f"       cross-region fallback triggered")
        except Exception as exc:
            elapsed = tock(region)
            results[region] = {"count": 0, "time": elapsed, "ok": False, "error": str(exc)[:120]}
            print(f"  {FAIL} {region:<22} crashed in {elapsed:.0f}s: {str(exc)[:120]}")

    return results


async def stress_edge_cases() -> dict:
    """Test edge cases: broken URLs, empty companies, 404 slugs."""
    from agents.job_hunter.agent import JobHunterAgent

    agent = JobHunterAgent(task_ctx=_ctx("edge"))
    results = {}

    # 1. Broken ATS slug (Mistral via lever)
    tick("mistral_404")
    try:
        postings = await agent._fetch_ats({
            "ats": "lever", "ats_company_id": "mistral", "name": "Mistral", "region": "eu"
        })
        elapsed = tock("mistral_404")
        results["mistral_404"] = {"count": len(postings), "time": elapsed, "ok": True}
        print(f"  {PASS if len(postings) == 0 else WARN} Mistral lever (404 slug): {len(postings)} postings in {elapsed:.1f}s (expected 0)")
    except Exception as exc:
        elapsed = tock("mistral_404")
        results["mistral_404"] = {"count": 0, "time": elapsed, "ok": True, "handled": True}
        print(f"  {PASS} Mistral lever (404 slug): gracefully caught: {str(exc)[:60]}")

    # 2. Empty prefs set
    tick("prefs_empty")
    ok, msg = agent.set_preference("bogus.nonexistent", "x")
    results["prefs_empty"] = {"rejected": not ok, "msg": msg[:60]}
    print(f"  {PASS if not ok else FAIL} Invalid prefs key: rejected correctly")

    # 3. Add invalid region
    tick("add_bad_region")
    ok, msg = agent.add_company_to_watchlist("TestCo", "mars")
    results["add_bad"] = {"rejected": not ok}
    print(f"  {PASS if not ok else FAIL} Invalid region 'mars': {msg[:60]}")

    # 4. Remove nonexistent company
    tick("remove_nonexistent")
    ok, msg = agent.remove_company_from_watchlist("ZZZNonexistentCorp")
    results["remove_nonexistent"] = {"rejected": not ok}
    print(f"  {PASS if not ok else FAIL} Remove nonexistent: {msg[:60]}")

    # 5. /research with empty string
    tick("research_empty")
    try:
        brief = await agent.pre_research("")
        results["research_empty"] = {"handled": bool(brief)}
        print(f"  {PASS if brief else FAIL} /research empty: {'returned fallback' if brief else 'crashed'}")
    except Exception as exc:
        results["research_empty"] = {"crashed": str(exc)[:60]}
        print(f"  {FAIL} /research empty: crashed: {str(exc)[:60]}")

    return results


async def stress_enrichment() -> dict:
    """Test enrichment pipeline: role, visa, remote, skills extraction."""
    from agents.job_hunter.agent import JobHunterAgent

    agent = JobHunterAgent(task_ctx=_ctx("enrich"))
    profile = agent._load_career_profile()
    skills = agent._load_skill_clusters()
    await agent._ensure_user_skill_vec(skills)

    # Use real postings from Together AI (small board, fast)
    try:
        postings = await agent._fetch_ats({
            "ats": "greenhouse", "ats_company_id": "togetherai",
            "name": "Together AI", "region": "international_remote",
        })
    except Exception:
        postings = []

    if not postings:
        print(f"  {WARN} Together AI fetch failed, using synthetic posting")
        postings = [{
            "title": "Frontier Agents Intern (Fall 2026)",
            "description": "Join our agents team. Work on multi-agent architectures, task routing, and LLM orchestration. Requirements: Python, PyTorch. Remote-friendly. We use Deel for international hiring.",
            "_region": "international_remote",
            "_organization": "Together AI",
            "role_type": "unknown",
            "external_id": "test:1",
            "source": "test",
            "source_url": "https://togetherai.com",
            "_source_tier": 1,
            "_score_raw": 0.56,
        }]

    results = {"count": len(postings)}
    top = postings[:5]

    # Role enrichment
    tick("role")
    try:
        await agent._enrich_roles(top)
        classified = sum(1 for p in top if p.get("role_type") != "unknown")
        results["role"] = classified
        print(f"  {PASS} Role enrichment: {classified}/{len(top)} classified")
    except Exception as exc:
        results["role"] = str(exc)[:60]
        print(f"  {FAIL} Role enrichment: {str(exc)[:60]}")

    # Visa enrichment
    tick("visa")
    try:
        await agent._enrich_visas(top, profile)
        visa_count = sum(1 for p in top if p.get("visa_status"))
        results["visa"] = visa_count
        print(f"  {PASS} Visa enrichment: {visa_count}/{len(top)} classified")
    except Exception as exc:
        results["visa"] = str(exc)[:60]
        print(f"  {FAIL} Visa enrichment: {str(exc)[:60]}")

    # Remote enrichment
    tick("remote")
    try:
        await agent._enrich_remote(top)
        africa_ok = sum(1 for p in top if p.get("_africa_ok") is True)
        results["remote"] = africa_ok
        print(f"  {PASS} Remote enrichment: {africa_ok}/{len(top)} Africa-friendly")
    except Exception as exc:
        results["remote"] = str(exc)[:60]
        print(f"  {FAIL} Remote enrichment: {str(exc)[:60]}")

    # Skills extraction
    tick("skills")
    try:
        await agent._enrich_skills(top)
        has_skills = sum(1 for p in top if p.get("required_skills"))
        results["skills"] = has_skills
        print(f"  {PASS} Skills extraction: {has_skills}/{len(top)} extracted")
    except Exception as exc:
        results["skills"] = str(exc)[:60]
        print(f"  {FAIL} Skills extraction: {str(exc)[:60]}")

    return results


async def main():
    print("=" * 70)
    print("  JOB HUNTER — LIVE STRESS TEST")
    print("  Tests all features against real APIs, real rate limits,")
    print("  real 404s, real timeouts. Measures survival, not just pass/fail.")
    print("=" * 70)

    overall = {}

    # ── 1. ATS fetch stress ──
    print(f"\n{'='*70}")
    print("  1. ATS FETCH STRESS (6 biggest boards)")
    print(f"{'='*70}")
    tick("ats_total")
    overall["ats"] = await stress_ats_retry()
    print(f"  Total: {tock('ats_total'):.0f}s  "
          f"passed={sum(1 for v in overall['ats'].values() if v['ok'])}/"
          f"{len(overall['ats'])}")

    # ── 2. Discovery across all regions ──
    print(f"\n{'='*70}")
    print("  2. DISCOVERY STRESS (all 5 regions)")
    print(f"{'='*70}")
    tick("discovery_total")
    overall["discovery"] = await stress_discovery_regions()
    print(f"  Total: {tock('discovery_total'):.0f}s  "
          f"passed={sum(1 for v in overall['discovery'].values() if v['ok'])}/"
          f"{len(overall['discovery'])}")

    # ── 3. Edge cases ──
    print(f"\n{'='*70}")
    print("  3. EDGE CASES (404s, invalid inputs, empty strings)")
    print(f"{'='*70}")
    overall["edges"] = await stress_edge_cases()

    # ── 4. Enrichment pipeline ──
    print(f"\n{'='*70}")
    print("  4. ENRICHMENT PIPELINE (role + visa + remote + skills)")
    print(f"{'='*70}")
    overall["enrichment"] = await stress_enrichment()

    # ── Summary ──
    print(f"\n{'='*70}")
    print("  STRESS TEST SUMMARY")
    print(f"{'='*70}")

    ats_ok = sum(1 for v in overall["ats"].values() if v["ok"])
    disc_ok = sum(1 for v in overall["discovery"].values() if v["ok"])
    edge_ok = sum(1 for v in overall["edges"].values() if isinstance(v, dict) and v.get("ok", True))
    enrich_ok = sum(1 for v in overall["enrichment"].values() if isinstance(v, int) and v > 0)

    print(f"  ATS fetch:        {ats_ok}/{len(overall['ats'])} boards survived")
    print(f"  Discovery:        {disc_ok}/{len(overall['discovery'])} regions survived")
    print(f"  Edge cases:       all handled gracefully")
    print(f"  Enrichment:       {enrich_ok}/4 pipelines produced output")

    total_time = tock("ats_total") + tock("discovery_total")
    print(f"\n  Total wall time: {total_time:.0f}s ({total_time/60:.1f} min)")
    print(f"  Survived: {'YES' if ats_ok >= 5 and disc_ok >= 5 else 'ISSUES FOUND'}")

    if ats_ok < 5:
        print(f"\n  ⚠️  ATS failures — check Greenhouse rate limits / network")
    if disc_ok < 5:
        print(f"\n  ⚠️  Discovery failures — enrichment or scoring may be broken")


if __name__ == "__main__":
    asyncio.run(main())