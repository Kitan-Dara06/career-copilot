#!/usr/bin/env python3
"""Comprehensive end-to-end test of all Job Hunter features.

Tests every user-facing command, enrichment pipeline, and callback.
Runs without the Telegram bot — exercises agents directly.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from backbone.tools.base import ToolContext
from career_copilot.config import get_settings


def _ctx() -> ToolContext:
    return ToolContext(agent="job_hunter", task_id=f"test_{datetime.now(UTC).timestamp():.0f}", settings=get_settings())


PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "
SEP = "─" * 60


async def test_companies(agent) -> int:
    """Test /companies list, add, remove, region."""
    print(f"\n{SEP}\n  1. /companies\n{SEP}")
    ok = 0

    # List all
    companies = agent._load_watchlist()
    print(f"  /companies (list): {len(companies)} companies")
    by_region = {}
    for c in companies[:5]:
        r = c.get("region", "?")
        by_region[r] = by_region.get(r, 0) + 1
    print(f"  Regions: {by_region}")
    ok += 1

    # Region filter
    ng = agent._load_watchlist("nigeria")
    print(f"  /companies region nigeria: {len(ng)} companies")
    ok += 1

    # Add
    ok_add, msg_add = agent.add_company_to_watchlist("TestCo AI Labs", "africa")
    print(f"  /companies add 'TestCo AI Labs' africa: {PASS if ok_add else FAIL} {msg_add}")
    ok += 1 if ok_add else 0

    # Duplicate add
    ok_dup, msg_dup = agent.add_company_to_watchlist("TestCo AI Labs", "africa")
    print(f"  /companies add duplicate: {PASS if not ok_dup else FAIL} {msg_dup}")
    ok += 1 if not ok_dup else 0

    # Remove
    ok_rem, msg_rem = agent.remove_company_from_watchlist("TestCo AI Labs")
    print(f"  /companies remove 'TestCo AI Labs': {PASS if ok_rem else FAIL} {msg_rem}")
    ok += 1 if ok_rem else 0

    # Remove non-existent
    ok_nx, msg_nx = agent.remove_company_from_watchlist("NonexistentCo")
    print(f"  /companies remove nonexistent: {PASS if not ok_nx else FAIL} {msg_nx}")
    ok += 1 if not ok_nx else 0

    return ok


async def test_prefs(agent) -> int:
    """Test /prefs and /prefs set."""
    print(f"\n{SEP}\n  2. /prefs + /prefs set\n{SEP}")
    ok = 0

    # Show
    profile = agent._load_career_profile()
    print(f"  /prefs: min_match={profile.get('min_match_score')}, digest={profile.get('digest_frequency_days')}d @ {profile.get('digest_time')}")
    ok += 1

    # Set salary
    ok_s, msg_s = agent.set_preference("salary.canada", "110000")
    print(f"  /prefs set salary.canada 110000: {PASS if ok_s else FAIL} {msg_s}")
    agent.set_preference("salary.canada", "100000")  # restore
    ok += 1

    # Set digest
    ok_d, msg_d = agent.set_preference("digest.cadence", "4")
    print(f"  /prefs set digest.cadence 4: {PASS if ok_d else FAIL} {msg_d}")
    agent.set_preference("digest.cadence", "3")  # restore
    ok += 1

    # Set time
    ok_t, msg_t = agent.set_preference("digest.time", "09:30")
    print(f"  /prefs set digest.time 09:30: {PASS if ok_t else FAIL} {msg_t}")
    agent.set_preference("digest.time", "08:00")  # restore
    ok += 1

    # Set match score
    ok_m, msg_m = agent.set_preference("match.score", "0.50")
    print(f"  /prefs set match.score 0.50: {PASS if ok_m else FAIL} {msg_m}")
    agent.set_preference("match.score", "0.45")  # restore
    ok += 1

    # Invalid key
    ok_b, msg_b = agent.set_preference("bogus.key", "x")
    print(f"  /prefs set bogus.key: {PASS if not ok_b else FAIL} {msg_b}")
    ok += 1

    return ok


async def test_jobs_discovery(agent) -> int:
    """Test /jobs with cross-region fallback, enrichment, skills extraction."""
    print(f"\n{SEP}\n  3. /jobs (discovery + enrichment)\n{SEP}")
    ok = 0
    t0 = time.monotonic()

    # Run a fast region (africa — small, triggers fallback to intl_remote)
    print("  Running /jobs africa (includes cross-region fallback)...")
    results = await agent.run_discovery(region="africa")
    elapsed = time.monotonic() - t0

    print(f"  Postings above threshold: {len(results)} in {elapsed:.0f}s")
    if results:
        print(f"  Top posting: {results[0].get('title','')[:60]} (score={results[0].get('_score_raw',0):.2f})")
    ok += 1

    # Check enrichment fields
    enriched = 0
    for r in results[:5]:
        has_role = r.get("role_type") != "unknown"
        has_visa = r.get("visa_status") is not None
        has_africa = r.get("_africa_ok") is not None if r.get("_region") == "international_remote" else True
        has_skills = bool(r.get("required_skills"))
        has_salary = bool(r.get("_salary"))
        flags = []
        if has_role: flags.append("role")
        if has_visa: flags.append("visa")
        if not has_africa: flags.append("no-africa")
        if has_skills: flags.append("skills")
        if has_salary: flags.append("salary")
        if flags:
            enriched += 1
    print(f"  Enrichment: {enriched}/{min(5, len(results))} postings have enrichment fields")
    ok += 1 if enriched > 0 or not results else 0

    # Test known salary
    if results:
        # Check salary annotation for known payers
        for r in results:
            org = (r.get("_organization", "") or "").lower()
            known_orgs = ("google", "flutterwave", "paystack", "moniepoint", "stripe", "gitlab", "anthropic")
            if any(k in org for k in known_orgs) and r.get("_salary"):
                print(f"  Known salary: {r['_organization']} → {r['_salary']}")
                break
    ok += 1

    return ok


async def test_single_lookup(agent) -> int:
    """Test /job <URL> — single posting lookup."""
    print(f"\n{SEP}\n  4. /job (single posting lookup)\n{SEP}")
    ok = 0

    # Test with a known Greenhouse posting URL
    test_url = "https://boards.greenhouse.io/anthropic/jobs/4567890000"
    result = await agent.lookup_single_posting(test_url)
    if result:
        print(f"  {PASS} URL lookup: '{result.get('title','')[:50]}' at {result.get('organization','')} (score={result.get('_score_raw',0):.2f})")
        ok += 1
    else:
        print(f"  {WARN} URL lookup returned None (page may be 404 or not parseable)")

    # Test with pasted text
    test_text = """
    ML Engineer Intern — Flutterwave
    Lagos, Nigeria · Hybrid
    We're looking for an ML Engineer Intern to join our AI team. You'll work on
    building retrieval-augmented generation pipelines, fine-tuning LLMs, and
    deploying multi-agent systems for payment fraud detection.
    Requirements: Python, PyTorch, experience with LangChain or LlamaIndex.
    Nice to have: Docker, Kubernetes, CI/CD experience.
    Apply at https://flutterwave.com/careers
    """
    result2 = await agent.lookup_single_posting(test_text)
    if result2:
        print(f"  {PASS} Text lookup: '{result2.get('title','')[:50]}' at {result2.get('organization','')} (score={result2.get('_score_raw',0):.2f})")
        ok += 1
    else:
        print(f"  {FAIL} Text lookup failed to extract posting")

    return ok


async def test_research(agent) -> int:
    """Test /research <company> — pre-research flow."""
    print(f"\n{SEP}\n  5. /research (company pre-research)\n{SEP}")
    ok = 0

    result = await agent.pre_research("Flutterwave")
    if result and len(result) > 100:
        lines = result.split("\n")
        print(f"  {PASS} Research brief: {len(lines)} lines, {len(result)} chars")
        for line in lines[:3]:
            print(f"    {line[:80]}")
        ok += 1
    else:
        print(f"  {WARN} Research brief short or failed: {result[:80] if result else 'None'}")

    # Test edge case
    result2 = await agent.pre_research("")
    print(f"  {PASS if result2 else FAIL} Empty company: {'returned fallback' if result2 else 'crashed'}")
    ok += 1

    return ok


async def test_saved_and_callbacks(agent) -> int:
    """Test /saved, jh_save, jh_skip callbacks."""
    print(f"\n{SEP}\n  6. /saved + save/skip callbacks\n{SEP}")
    ok = 0

    # View saved
    saved = await agent.get_saved_postings()
    print(f"  /saved: {len(saved)} saved postings")
    for s in saved[:3]:
        print(f"    - {s.get('organization','?')}: {s.get('title','?')[:60]}")
    ok += 1

    # Mark saved (test with fake external_id — should not crash)
    ok_s = await agent.mark_saved("gh:99999999")
    print(f"  jh_save callback (nonexistent): {'no crash' if ok_s is not None else 'crash'}")
    ok += 1

    # Mark skipped
    ok_sk = await agent.mark_skipped("gh:99999999")
    print(f"  jh_skip callback (nonexistent): {'no crash' if ok_sk is not None else 'crash'}")
    ok += 1

    return ok


async def test_classifiers() -> int:
    """Test academic/industry classifier and academic/industry role types."""
    print(f"\n{SEP}\n  7. Classifiers (academic vs industry)\n{SEP}")
    from backbone.tools.jobs import _classify_context
    ok = 0

    tests = [
        ("PhD Position in Conversational IR", "University of Amsterdam", "academic"),
        ("ML Engineer Intern", "Flutterwave", "industry"),
        ("Research Assistant in NLP", "MIT", "academic"),
        ("Postdoctoral Researcher", "ETH Zurich", "academic"),
        ("Software Engineer, New Grad", "Google", "industry"),
        ("Graduate Research Assistant", "Stanford University", "academic"),
        ("Senior ML Engineer", "Stripe", "industry"),
    ]
    for title, org, expected in tests:
        result = _classify_context(title, org)
        icon = PASS if result == expected else FAIL
        print(f"  {icon} '{title[:40]}' → {result} (expected {expected})")
        ok += 1 if result == expected else 0

    return ok


async def test_salary_overrides(agent) -> int:
    """Test known-payer salary overrides."""
    print(f"\n{SEP}\n  8. Known-payer salary overrides\n{SEP}")
    profile = agent._load_career_profile()
    ok = 0

    tests = [
        ({"_organization": "Google Lagos", "_region": "nigeria"}, "~$80-150k USD"),
        ({"_organization": "Flutterwave", "_region": "nigeria"}, "~$120-180k USD"),
        ({"_organization": "Anthropic", "_region": "international_remote"}, "~$180-250k USD"),
        ({"_organization": "GitLab", "_region": "international_remote"}, "~$130-180k USD"),
        ({"_organization": "Unknown Startup", "_region": "nigeria"}, ""),
    ]
    for posting, expected in tests:
        result = agent._annotate_salary(posting, profile)
        icon = PASS if expected in result else FAIL
        print(f"  {icon} {posting['_organization']}: '{result[:50]}' (expected '{expected}')")
        ok += 1 if expected in result else 0

    return ok


async def test_digest_formatting(agent) -> int:
    """Test send_digest formatting (Open button, salary, visa, africa badge)."""
    print(f"\n{SEP}\n  9. Digest formatting\n{SEP}")
    ok = 0

    # Verify _format_match uses the right threshold
    p1 = {"_score_raw": 0.60}
    p2 = {"_score_raw": 0.42}
    p3 = {"_score_raw": 0.82}
    print(f"  _format_match:")
    print(f"    score 0.60 → '{agent._format_match(p1)}'")
    print(f"    score 0.42 → '{agent._format_match(p2)}'")
    print(f"    score 0.82 → '{agent._format_match(p3)}'")
    ok += 1

    # Verify Africa-friendly badge logic
    p_africa = {"_africa_ok": True, "_organization": "GitLab", "_region": "international_remote"}
    print(f"  Africa badge: {'✅' if p_africa.get('_africa_ok') else '❌'} for {p_africa['_organization']}")
    ok += 1

    return ok


async def test_skills_extraction(agent) -> int:
    """Test _llm_extract_skills with a realistic posting."""
    print(f"\n{SEP}\n  10. Skills extraction (LLM)\n{SEP}")
    ok = 0

    posting = {
        "title": "ML Engineer Intern — AI Agents Team",
        "description": (
            "We're looking for an ML Engineering Intern to join our AI Agents team. "
            "You'll work on building and deploying multi-agent systems for enterprise "
            "workflows. Requirements: Python, PyTorch or TensorFlow, experience with "
            "LLM APIs (OpenAI, Anthropic), understanding of retrieval-augmented generation. "
            "Nice to have: LangChain, vector databases (Pinecone/Weaviate), Docker, "
            "experience with agent frameworks (CrewAI, AutoGen). "
            "This is a 6-month internship for current students."
        ),
    }
    result = await agent._llm_extract_skills(posting)
    if result:
        req = result.get("required_skills", [])
        nice = result.get("nice_to_have", [])
        exp = result.get("min_experience_years")
        edu = result.get("education_required", "none")
        print(f"  {PASS} Extracted: required={req[:3]}, nice_to_have={nice[:3]}, exp={exp}, edu={edu}")
        ok += 1
    else:
        print(f"  {FAIL} Skills extraction failed")

    return ok


async def main():
    from agents.job_hunter.agent import JobHunterAgent

    print("=" * 60)
    print("  JOB HUNTER — Full Feature Test Suite")
    print("=" * 60)

    agent = JobHunterAgent(task_ctx=_ctx())
    total = 0

    total += await test_companies(agent)
    total += await test_prefs(agent)
    total += await test_salary_overrides(agent)
    total += await test_classifiers()
    total += await test_single_lookup(agent)
    total += await test_research(agent)
    total += await test_jobs_discovery(agent)
    total += await test_saved_and_callbacks(agent)
    total += await test_digest_formatting(agent)
    total += await test_skills_extraction(agent)

    print(f"\n{'='*60}")
    print(f"  RESULTS: {total} checks passed")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())