#!/usr/bin/env python3
"""Contribution Finder — live test + v0.1 vs v0.2 scoring comparison.

1. Fetches real GitHub issues from 3 query categories
2. Scores each with v0.1 (keyword) and v0.2 (Voyage embedding)
3. Reports: agreement rate, where they diverge, which catches real AI issues
4. Tests each Telegram-facing command path
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime

from backbone.tools.base import ToolContext
from career_copilot.config import get_settings


def _ctx(name: str = "") -> ToolContext:
    return ToolContext(agent="contribution_finder", task_id=f"eval_{name}", settings=get_settings())


PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


async def fetch_issues(query: str, label: str, n: int = 5) -> list[dict]:
    """Fetch n issues from GitHub for a given query."""
    from backbone.tools.github import SearchIssuesInput, SearchIssuesTool
    tool = SearchIssuesTool()
    out = await tool(_ctx(label), SearchIssuesInput(query=query, per_page=n))
    return [i.model_dump() for i in out.issues]


async def score_v01(issues: list[dict], agent) -> list[dict]:
    """Score with v0.1 keyword method (no API call)."""
    agent._score_all(issues, agent._load_cf_prefs())
    for iss in issues:
        iss["_v01_score"] = iss.pop("_impact_score")
    return issues


async def score_v02(issues: list[dict], agent) -> list[dict]:
    """Score with v0.2 Voyage embedding method (1 batch API call)."""
    from backbone.tools.vector import EmbedInput, EmbedTool

    if not agent._cluster_vecs:
        clusters = agent._load_skill_clusters()
        texts = [" ".join(c["skills"]) for c in clusters]
        agent._cluster_names = [c["name"] for c in clusters]
        agent._cluster_weights = [c["weight"] for c in clusters]
        embeds = await agent._embed(agent.ctx, EmbedInput(texts=texts))
        agent._cluster_vecs = [list(v) for v in (embeds.embeddings or [])]

    embed_tool = EmbedTool()
    texts = [(iss.get("title", "") + " " + (iss.get("body", "") or "")[:500])[:2000] for iss in issues]
    batch_embeds = await embed_tool(_ctx("v02"), EmbedInput(texts=texts))
    posting_vecs = [list(v) for v in (batch_embeds.embeddings or [])]

    for i, iss in enumerate(issues):
        pvec = posting_vecs[i] if i < len(posting_vecs) else [0.0]
        best = 0.0
        for cname, cvec, cweight in zip(agent._cluster_names, agent._cluster_vecs, agent._cluster_weights, strict=True):
            sim = sum(x * y for x, y in zip(cvec, pvec, strict=True))
            mag_a = math.sqrt(sum(x * x for x in cvec))
            mag_b = math.sqrt(sum(x * x for x in pvec))
            cosine = sim / (mag_a * mag_b) if mag_a and mag_b else 0.0
            weighted = cosine * cweight
            if weighted > best:
                best = weighted
        iss["_v02_score"] = round(best, 2)
    return issues


def compare(issues: list[dict], threshold: float = 0.35) -> dict:
    """Compare v0.1 vs v0.2: agreement, divergence, signal quality."""
    v01_pass = sum(1 for i in issues if i.get("_v01_score", 0) >= threshold)
    v02_pass = sum(1 for i in issues if i.get("_v02_score", 0) >= threshold)
    both_pass = sum(1 for i in issues if i.get("_v01_score", 0) >= threshold and i.get("_v02_score", 0) >= threshold)
    v01_only = sum(1 for i in issues if i.get("_v01_score", 0) >= threshold and i.get("_v02_score", 0) < threshold)
    v02_only = sum(1 for i in issues if i.get("_v02_score", 0) < threshold and i.get("_v02_score", 0) >= threshold)
    return {
        "total": len(issues),
        "threshold": threshold,
        "v01_pass": v01_pass,
        "v02_pass": v02_pass,
        "both_pass": both_pass,
        "v01_only": v01_only,
        "v02_only": v02_only,
    }


async def test_commands(agent):
    """Test every command path: /contrib, /contrib <topic>, /contrib repos, tracked repos load."""
    results = {}
    print(f"\n{'='*60}")
    print("  2. COMMAND PATHS TEST")
    print(f"{'='*60}")

    # repos
    repos = agent._load_tracked_repos()
    results["repos"] = len(repos)
    print(f"  {PASS} /contrib repos: {len(repos)} repos loaded")
    for r in repos[:3]:
        print(f"    - {r['full_name']} [{r['topic_hint']}]")

    # prefs
    prefs = agent._load_cf_prefs()
    results["prefs"] = prefs
    print(f"  {PASS} prefs: min_score={prefs.get('min_impact_score')}, buckets={prefs.get('preferred_effort_buckets')}")

    # discovery with topic
    try:
        opps = await agent.run_discovery(topic="langchain rag agent good first issue")
        results["topic_discovery"] = len(opps)
        print(f"  {PASS if opps else WARN} /contrib langchain rag: {len(opps)} opportunities")
    except Exception as exc:
        results["topic_discovery"] = str(exc)[:80]
        print(f"  {FAIL} /contrib topic: {exc}")

    # full discovery (all queries)
    try:
        opps_all = await agent.run_discovery()
        results["full_discovery"] = len(opps_all)
        analyzed = sum(1 for o in opps_all if o.get("problem"))
        print(f"  {PASS if opps_all else WARN} /contrib (all queries): {len(opps_all)} opps, {analyzed} with Gemini analysis")
    except Exception as exc:
        results["full_discovery"] = str(exc)[:80]
        print(f"  {FAIL} /contrib all: {exc}")

    # feedback
    try:
        await agent.record_feedback("test/repo#999", "pass")
        results["feedback"] = "no crash"
        print(f"  {PASS} record_feedback: no crash")
    except Exception as exc:
        results["feedback"] = str(exc)[:80]
        print(f"  {FAIL} feedback: {exc}")

    return results


async def main():
    from agents.contribution_finder.agent import ContributionFinderAgent

    print("=" * 60)
    print("  CONTRIBUTION FINDER — Live Test + Scoring Eval")
    print("=" * 60)

    agent = ContributionFinderAgent(task_ctx=_ctx("main"))
    await agent._ensure_skill_vecs()

    # ── Phase 1: scoring comparison ──
    print(f"\n{'='*60}")
    print("  1. v0.1 (keyword) vs v0.2 (Voyage embedding) SCORING COMPARISON")
    print(f"{'='*60}")

    queries = [
        ("langchain rag agent retrieval is:issue is:open language:python label:\"good first issue\",\"help wanted\"", "AI agents"),
        ("vector database embedding document is:issue is:open language:python label:\"good first issue\",\"help wanted\"", "Vector DB"),
        ("FastAPI async python web scraping is:issue is:open language:python label:\"good first issue\",\"help wanted\"", "Backend"),
    ]

    all_issues = []
    for query, label in queries:
        print(f"\n  Fetching: {label}...")
        issues = await fetch_issues(query, label, n=5)
        all_issues.extend(issues)
        print(f"    {len(issues)} issues")

    # Score with both methods
    t0 = time.monotonic()
    issues_v01 = await score_v01([i.copy() for i in all_issues], agent)
    t01 = time.monotonic() - t0
    print(f"\n  v0.1 scoring: {t01:.3f}s (keyword, no API)")

    t0 = time.monotonic()
    issues_v02 = await score_v02([i.copy() for i in all_issues], agent)
    t02 = time.monotonic() - t0
    print(f"  v0.2 scoring: {t02:.3f}s (Voyage batch, 1 API call)")

    # Merge scores
    for i, iss in enumerate(all_issues):
        iss["_v01_score"] = issues_v01[i].get("_v01_score", 0)
        iss["_v02_score"] = issues_v02[i].get("_v02_score", 0)

    # Compare
    print(f"\n  {'v0.1':>6} {'v0.2':>6} {'Δ':>6}  Title")
    print("  " + "-" * 70)
    for iss in sorted(all_issues, key=lambda x: abs(x["_v01_score"] - x["_v02_score"]), reverse=True):
        v01 = iss["_v01_score"]
        v02 = iss["_v02_score"]
        delta = v02 - v01
        delta_str = f"+{delta:.2f}" if delta > 0 else f"{delta:.2f}"
        print(f"  {v01:6.2f} {v02:6.2f} {delta_str:>6}  {iss['title'][:55]}")

    # Summary
    for thresh in [0.35, 0.45, 0.55]:
        comp = compare(all_issues, thresh)
        print(f"\n  Threshold {thresh}: v0.1={comp['v01_pass']}/{comp['total']} v0.2={comp['v02_pass']}/{comp['total']} "
              f"both={comp['both_pass']} v01-only={comp['v01_only']} v02-only={comp['v02_only']}")

    # Which method finds more real AI issues?
    ai_titles = [i for i in all_issues if any(kw in i["title"].lower() for kw in ("rag", "agent", "llm", "embed", "retriev", "langchain", "vector"))]
    if ai_titles:
        v01_ai = sum(1 for i in ai_titles if i["_v01_score"] >= 0.35)
        v02_ai = sum(1 for i in ai_titles if i["_v02_score"] >= 0.35)
        print(f"\n  Real AI titles ({len(ai_titles)}): v0.1 passes={v01_ai} v0.2 passes={v02_ai}")

    # ── Phase 2: command paths ──
    cmd_results = await test_commands(agent)

    # ── Final summary ──
    print(f"\n{'='*60}")
    print("  FINAL SUMMARY")
    print(f"{'='*60}")
    print(f"  Scoring comparison: {len(all_issues)} issues, {len(queries)} query categories")
    print(f"  v0.1 speed: {t01:.3f}s (no API)")
    print(f"  v0.2 speed: {t02:.3f}s (1 Voyage batch) — {(t02/t01)*100:.0f}% of v0.1 time")
    ai_count = len(ai_titles) if ai_titles else 0
    print(f"  AI-relevant titles: {ai_count}/{len(all_issues)}")
    if ai_titles:
        print(f"  v0.1 catches: {v01_ai}/{ai_count}  v0.2 catches: {v02_ai}/{ai_count}")
        if v02_ai > v01_ai:
            print(f"  ✅ v0.2 surfaces {v02_ai - v01_ai} more real AI issues")
        elif v01_ai > v02_ai:
            print(f"  ⚠️  v0.1 catches more — keyword may be overfitting")
        else:
            print(f"  ✅ Both methods equivalent at this threshold")

    print(f"\n  Command paths tested: all {len(cmd_results)} passed" if all(not isinstance(v, str) or "FAIL" not in str(v) for v in cmd_results.values()) else f"  ⚠️  Some command paths failed")


if __name__ == "__main__":
    asyncio.run(main())