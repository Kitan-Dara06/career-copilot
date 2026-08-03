#!/usr/bin/env python3
"""Broad discovery worker — searches the open web for AI/ML postings matching Aaliyah's profile.

Unlike /jobs (watchlist-only), this casts a wide Tavily net with queries like
"applied AI engineer internship 2025", "ML engineer remote junior", etc.
Each result URL is Firecrawl-scraped, then DeepSeek v4-pro extracts structured
job posting data. Results scoring above the user's match threshold are
persisted to job_hunter_openings and surfaced in the next /jobs digest.

Runs as a standalone background task:
    uv run python scripts/broad_discovery_worker.py       # one-shot
    uv run python scripts/broad_discovery_worker.py --loop  # continuous, runs every 6h

Model: DeepSeek v4-pro for extraction (reliable structured output).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── Discovery queries ──────────────────────────────────────────────────────────
# Rotate through these. Each run picks one query set from the rotation.
# Exclude known job boards from Tavily results via -site: operators.
_JOB_BOARD_EXCLUSIONS = (
    " -site:linkedin.com -site:indeed.com -site:glassdoor.com"
    " -site:ziprecruiter.com -site:dice.com -site:monster.com"
    " -site:simplyhired.com -site:remoterocketship.com"
    " -site:workingnomads.com -site:remoteok.com -site:weworkremotely.com"
)

QUERY_SETS: list[list[str]] = [
    # Entry-level / internship
    [
        "applied AI engineer internship 2025 remote",
        "ML engineer intern fall 2025",
        "machine learning internship Africa remote",
        "research intern large language models 2025",
        "AI residency program 2025 entry level",
    ],
    # Agent / RAG roles
    [
        "AI agent engineer new grad 2025",
        "RAG engineer internship remote",
        "LLM application engineer entry level",
        "generative AI engineer junior remote",
        "prompt engineer internship 2025",
    ],
    # Africa-specific
    [
        "AI engineer Nigeria remote 2025",
        "machine learning Lagos internship",
        "data scientist Kenya AI startup",
        "AI research assistant Africa remote",
        "NLP engineer South Africa internship",
    ],
    # Remote-first / global
    [
        "\"work from anywhere\" AI engineer junior",
        "\"remote first\" machine learning intern 2025",
        "\"global remote\" LLM engineer entry level",
        "\"EMEA\" AI research internship",
        "\"any timezone\" software engineer AI ML",
    ],
]


async def discover_broad() -> list[dict[str, Any]]:
    """Run one broad discovery pass. Returns new postings above threshold."""
    from agents.job_hunter.agent import JobHunterAgent, _cosine
    from backbone.model_client import ModelClient, parse_loose_json
    from backbone.tools.base import ToolContext
    from backbone.tools.firecrawl import FirecrawlScrapeTool, ScrapeInput
    from backbone.tools.tavily import SearchInput, TavilySearchTool
    from backbone.tools.vector import EmbedInput, EmbedTool
    from career_copilot.config import get_settings

    settings = get_settings()
    ctx = ToolContext(agent="job_hunter", task_id=f"broad_{datetime.now(UTC).timestamp():.0f}", settings=settings)

    agent = JobHunterAgent(task_ctx=ctx)
    profile = agent._load_career_profile()
    skills = agent._load_skill_clusters()
    await agent._ensure_user_skill_vec(skills)
    min_match = profile.get("min_match_score", 0.45)

    # Pick query set (rotate by day-of-year).
    day_idx = datetime.now(UTC).timetuple().tm_yday % len(QUERY_SETS)
    queries = QUERY_SETS[day_idx]
    print(f"[broad] Query set {day_idx+1}/{len(QUERY_SETS)}: {len(queries)} queries")

    tavily = TavilySearchTool()
    firecrawl = FirecrawlScrapeTool()
    llm = ModelClient()
    embed = EmbedTool()

    # Phase 1: Tavily search → collect unique URLs.
    scraped_urls: set[str] = set()
    all_snippets: list[dict[str, str]] = []  # {url, title, snippet}

    for raw_query in queries:
        query = raw_query + _JOB_BOARD_EXCLUSIONS
        print(f"[broad] Tavily: {query[:70]}...")
        try:
            out = await tavily(ctx, SearchInput(query=query, max_results=5))
        except Exception as exc:
            print(f"[broad]   ! failed: {exc}")
            continue
        for r in (out.results or []):
            url = r.url
            if not url or url in scraped_urls:
                continue
            scraped_urls.add(url)
            all_snippets.append({
                "url": url,
                "title": r.title or "",
                "snippet": r.content or "",
            })
        print(f"[broad]   → {len(out.results)} results")
        await asyncio.sleep(1)  # rate-limit Tavily

    print(f"[broad] {len(scraped_urls)} unique URLs to scrape")

    # Phase 2: Firecrawl scrape each URL → extract posting data with DeepSeek v4-pro.
    new_postings: list[dict[str, Any]] = []
    extraction_prompt = _load_extraction_prompt()

    for i, snip in enumerate(all_snippets):
        url = snip["url"]
        print(f"[broad] [{i+1}/{len(all_snippets)}] scraping {url[:80]}...")
        try:
            scrape_out = await firecrawl(ctx, ScrapeInput(url=url, formats=["markdown"]))
            markdown = (scrape_out.content.markdown or "")[:8000]
            if not markdown or len(markdown) < 200:
                print(f"[broad]   ! too short ({len(markdown)} chars)")
                continue
        except Exception as exc:
            print(f"[broad]   ! scrape failed: {exc}")
            continue

        # Extract structured posting data with DeepSeek v4-pro.
        raw = await llm.generate(
            model="deepseek-v4-pro",
            prompt=extraction_prompt.format(
                url=url,
                title=snip["title"],
                snippet=snip["snippet"],
                content=markdown,
            ),
            temperature=0.1,
            max_tokens=600,
        )
        parsed = parse_loose_json(raw) if raw else None
        if not isinstance(parsed, dict):
            print(f"[broad]   ! extraction failed: {raw[:100] if raw else 'empty'}")
            continue

        is_job = parsed.get("is_job_posting", False)
        if not is_job:
            print(f"[broad]   ✗ not a job posting")
            continue

        title = parsed.get("title", snip["title"])[:200]
        org = parsed.get("organization", "")[:100]
        desc = parsed.get("description", "") or markdown[:2000]
        location = parsed.get("location", "")
        role = parsed.get("role_type", "unknown")
        remote = parsed.get("remote_ok")

        # Score against Aaliyah's profile.
        text = f"{title} {desc}"[:2000]
        posting_embeds = await embed(ctx, EmbedInput(texts=[text]))
        pvec = list(posting_embeds.embeddings[0]) if posting_embeds.embeddings else [0.0]
        score, top_cluster = agent._weighted_max_score(pvec)

        if score < min_match:
            print(f"[broad]   ✗ score {score:.2f} < {min_match} (top cluster: {top_cluster})")
            continue

        external_id = f"broad:{hashlib.sha256(url.encode()).hexdigest()[:16]}"
        posting = {
            "external_id": external_id,
            "source": "broad_discovery",
            "source_url": url,
            "title": title,
            "organization": org,
            "description": desc,
            "role_type": role,
            "location": location,
            "remote_ok": remote,
            "application_url": parsed.get("application_url", url),
            "_region": "international_remote",
            "_organization": org,
            "_source_tier": 3,
            "_score_raw": round(score, 2),
            "_top_cluster": top_cluster,
        }
        new_postings.append(posting)
        print(f"[broad]   ✅ score {score:.2f}  cluster={top_cluster}  {title[:60]}")

    print(f"[broad] Found {len(new_postings)} postings above threshold ({min_match})")

    # Phase 3: Persist to DB.
    if new_postings:
        await _persist_broad(new_postings)

    return new_postings


def _load_extraction_prompt() -> str:
    """Return the DeepSeek v4-pro extraction prompt template."""
    return """Extract structured job posting data from this web page. Use DeepSeek v4-pro precision.

URL: {url}
Page title: {title}
Search snippet: {snippet}

Page content (markdown):
---
{content}
---

Output STRICT JSON only — no preamble, no markdown fences.

Schema:
{{
  "is_job_posting": <true if this page is a job posting, false if it's a company blog, news article, or generic careers page>,
  "title": "<job title, max 150 chars>",
  "organization": "<company name>",
  "description": "<1-2 sentence summary of what the role does, max 500 chars>",
  "location": "<city, country or 'Remote'>",
  "remote_ok": <true|false|null>,
  "role_type": "<internship|co_op|new_grad|research|experienced|unknown>",
  "application_url": "<direct apply link or the page URL>"
}}"""


async def _persist_broad(postings: list[dict[str, Any]]) -> None:
    """Persist broad-discovered postings to job_hunter_openings."""
    from sqlalchemy import text
    from backbone.db.session import async_session_factory

    factory = async_session_factory()
    inserted = 0
    for p in postings:
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO job_hunter_openings"
                        " (external_id, source, source_url, title, organization,"
                        "  description, role_type, region, application_url, remote_ok)"
                        " VALUES"
                        " (:ext, :src, :url, :title, :org, :desc, :role, :region,"
                        "  :app, :remote)"
                        " ON CONFLICT (external_id) DO NOTHING"
                    ),
                    {
                        "ext": p["external_id"],
                        "src": p["source"],
                        "url": p["source_url"],
                        "title": p["title"][:1000],
                        "org": p["organization"][:500],
                        "desc": (p.get("description", "") or "")[:4000],
                        "role": p.get("role_type", "unknown"),
                        "region": p.get("_region", "international_remote"),
                        "app": p.get("application_url", ""),
                        "remote": p.get("remote_ok"),
                    },
                )
                await session.commit()
            inserted += 1
        except Exception:
            pass
    if inserted:
        print(f"[broad] Persisted {inserted} new postings to DB")


async def main():
    """Entry point. Handles --loop flag for continuous mode."""
    loop_mode = "--loop" in sys.argv

    if loop_mode:
        print("[broad] Continuous mode — running every 6 hours. Ctrl+C to stop.")
        while True:
            try:
                await discover_broad()
            except Exception as exc:
                print(f"[broad] ! cycle failed: {exc}")
            print(f"[broad] Sleeping 6 hours...")
            await asyncio.sleep(6 * 3600)
    else:
        await discover_broad()


if __name__ == "__main__":
    asyncio.run(main())
