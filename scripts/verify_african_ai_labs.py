#!/usr/bin/env python3
"""Verify African AI labs — check which ones have live careers pages with current AI/ML roles.

Each candidate gets probed:
  1. Try common careers-page URLs
  2. If 404, try Tavily search for "<company> careers"
  3. If found, Firecrawl scrape the page
  4. Check markdown for AI/ML/internship/engineering keywords
  5. Report: verified (add to watchlist) or dead/skip
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
WATCHLIST_PATH = DATA_DIR / "company_watchlist.yaml"

CANDIDATES = [
    # -- Nigeria --
    ("Intron Health", "nigeria", "Speech AI for African languages, YC-backed, Lagos"),
    ("Migo", "nigeria", "ML data platform, formerly Mines.io, Lagos"),
    ("Carbon (Paylater)", "nigeria", "Digital lending with ML credit scoring, Lagos"),
    ("Paga", "nigeria", "Mobile payments with ML fraud detection, Lagos"),
    ("Kuda ML Team", "nigeria", "Neobank with AI risk/ML teams, Lagos"),
    ("ThankUCash", "nigeria", "Loyalty platform, has data science roles, Lagos"),
    ("QuPre", "nigeria", "AI-powered document processing, Lagos"),
    ("Risevest", "nigeria", "Wealth management with ML, Lagos"),
    ("Busha", "nigeria", "Crypto exchange with ML, Lagos"),
    ("Patricia", "nigeria", "Crypto with ML fraud detection, Lagos"),
    # -- Kenya --
    ("Amini", "africa", "Geospatial AI for Africa, Nairobi"),
    ("Badili", "africa", "Refurbished phones, ML demand forecasting, Nairobi"),
    ("Lori Systems", "africa", "Logistics AI, Nairobi/Accra"),
    ("MarketForce", "africa", "Retail distribution AI, Nairobi"),
    ("Apollo Agriculture", "africa", "Agri-fintech with ML, Nairobi"),
    # -- South Africa --
    ("DataProphet", "africa", "Manufacturing AI, Cape Town"),
    ("Aerobotics", "africa", "Drone + crop AI, Cape Town"),
    ("Jumo", "africa", "Credit scoring ML, Cape Town"),
    ("Lelapa AI", "africa", "African language AI research, Johannesburg"),
    ("Spatialedge", "africa", "Geospatial ML, Stellenbosch"),
    # -- Egypt --
    ("Synapse Analytics", "africa", "AI platform for banking, Cairo"),
    ("Swvl", "africa", "Transit tech with AI optimization, Cairo"),
    ("Elmenus", "africa", "Food delivery with ML recommendations, Cairo"),
    # -- Ghana --
    ("mPharma Data", "africa", "Pharmacy supply chain AI, Accra"),
    ("Complete Farmer", "africa", "Agri-tech with ML, Accra"),
    # -- Rwanda/Kigali --
    ("Zipline Rwanda", "africa", "Drone delivery with ML routing, Kigali"),
    # -- Pan-Africa / Remote --
    ("InstaDeep BioNTech", "africa", "AI research lab, Lagos/Tunis/London, acquired by BioNTech"),
]

CAREERS_PATHS = [
    "/careers", "/jobs", "/about/careers", "/company/careers",
    "/careers/", "/jobs/", "/work-with-us", "/join-us", "/team",
]

AI_KEYWORDS = re.compile(
    r"(machine learning|ml engineer|data scientist|ai engineer|"
    r"software engineer|backend engineer|full.?stack|data engineer|"
    r"python|pytorch|intern|internship|research|"
    r"nlp|llm|agent|rag|retrieval|genai|computer vision)",
    re.IGNORECASE,
)


async def find_careers_page(company_name: str) -> str | None:
    """Find a company's careers page URL. Returns None if not found."""
    domain = company_name.lower().replace(" ", "").replace("(", "").replace(")", "")
    # Try common subdomains/domains
    candidates = [f"https://{domain}.com", f"https://www.{domain}.com",
                  f"https://{domain}.io", f"https://www.{domain}.io",
                  f"https://{domain}.co", f"https://www.{domain}.co",
                  f"https://{domain}.africa", f"https://www.{domain}.africa"]
    
    import httpx
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for base in candidates:
            for path in CAREERS_PATHS:
                url = f"{base}{path}"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200 and len(resp.text) > 500:
                        return url
                except Exception:
                    continue
    
    # Try Tavily search as fallback
    try:
        from backbone.tools.tavily import SearchInput, TavilySearchTool
        from backbone.tools.base import ToolContext
        from career_copilot.config import get_settings
        ctx = ToolContext(agent="job_hunter", task_id=f"verify_{company_name}", settings=get_settings())
        tool = TavilySearchTool()
        out = await tool(ctx, SearchInput(query=f"{company_name} careers jobs", max_results=3))
        for r in out.results:
            url = r.url
            if url and ("career" in url.lower() or "job" in url.lower() or "work" in url.lower()):
                return url
    except Exception:
        pass
    
    return None


async def verify_one(name: str, region: str, description: str) -> dict:
    """Verify one company. Returns {name, region, url, has_ai_roles, status}."""
    print(f"  {name:<30} ", end="", flush=True)
    
    url = await find_careers_page(name)
    if not url:
        print("❌ no careers page found")
        return {"name": name, "region": region, "url": None, "has_ai_roles": False, "status": "no_page"}
    
    # Try to scrape
    try:
        from backbone.tools.firecrawl import FirecrawlScrapeTool, ScrapeInput
        from backbone.tools.base import ToolContext
        from career_copilot.config import get_settings
        ctx = ToolContext(agent="job_hunter", task_id=f"verify_{name}", settings=get_settings())
        firecrawl = FirecrawlScrapeTool()
        scrape_out = await firecrawl(ctx, ScrapeInput(url=url, formats=["markdown"]))
        markdown = (scrape_out.content.markdown or "")[:5000]
    except Exception:
        print(f"⚠️  scrape failed, URL exists: {url}")
        return {"name": name, "region": region, "url": url, "has_ai_roles": False, "status": "scrape_failed"}

    # Check for AI/ML keywords
    has_ai = bool(AI_KEYWORDS.search(markdown))
    role_count = len(AI_KEYWORDS.findall(markdown))
    
    if has_ai:
        print(f"✅ {role_count} AI/ML signals, {url[:60]}")
        return {"name": name, "region": region, "url": url, "has_ai_roles": True, "status": "verified", "signals": role_count}
    else:
        # Check if it's a careers page at all (any job-related content)
        has_any_jobs = bool(re.search(r"(engineer|developer|designer|manager|analyst|intern|associate)", markdown, re.IGNORECASE))
        if has_any_jobs:
            print(f"⚠️  careers page exists but no AI/ML: {url[:60]}")
            return {"name": name, "region": region, "url": url, "has_ai_roles": False, "status": "no_ai_roles"}
        else:
            print(f"⚠️  page exists but no jobs listed: {url[:60]}")
            return {"name": name, "region": region, "url": url, "has_ai_roles": False, "status": "empty_page"}


async def main():
    print("=" * 70)
    print("  AFRICAN AI LABS — Verification Pass")
    print(f"  {len(CANDIDATES)} candidates to check")
    print("=" * 70)
    print()

    results = []
    for name, region, desc in CANDIDATES:
        r = await verify_one(name, region, desc)
        results.append(r)
        await asyncio.sleep(0.5)  # rate-limit

    # Summary
    verified = [r for r in results if r["status"] == "verified"]
    partial = [r for r in results if r["status"] in ("no_ai_roles", "scrape_failed", "empty_page")]
    dead = [r for r in results if r["status"] == "no_page"]

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  ✅ Verified (AI roles found):     {len(verified)}")
    print(f"  ⚠️  Partial (page exists, no AI):  {len(partial)}")
    print(f"  ❌ Dead (no careers page):         {len(dead)}")

    if verified:
        print(f"\n  Ready to add ({len(verified)}):")
        for r in verified:
            print(f"    - {r['name']} [{r['region']}] → {r['url']}")

    if partial:
        print(f"\n  Could add as Tier 2 anyway ({len(partial)}):")
        for r in partial:
            print(f"    - {r['name']} [{r['region']}] [{r['status']}] → {r['url']}")

    if dead:
        print(f"\n  Skip ({len(dead)}):")
        for r in dead:
            print(f"    - {r['name']}")

    return results


if __name__ == "__main__":
    asyncio.run(main())