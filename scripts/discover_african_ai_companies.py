#!/usr/bin/env python3
"""Self-expanding Tavily discovery loop — finds African AI companies not in watchlist.

Usage:
    uv run python scripts/discover_african_ai_companies.py           # print candidates
    uv run python scripts/discover_african_ai_companies.py --add      # append to watchlist
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
WATCHLIST_PATH = DATA_DIR / "company_watchlist.yaml"

DISCOVERY_QUERIES = [
    "AI startup hiring Nigeria 2025",
    "machine learning engineer Lagos careers",
    "LLM engineer Nigeria remote",
    "data science internship Lagos",
    "AI research lab Nigeria hiring",
    "machine learning engineer Nairobi careers 2025",
    "AI startup Kenya hiring",
    "data scientist Nairobi remote",
    "machine learning engineer Cape Town careers",
    "AI research internship South Africa",
    "machine learning engineer Cairo careers",
    "AI startup Egypt hiring 2025",
    "AI startup Accra hiring",
    "data scientist Ghana remote",
    "AI startup Kigali careers",
    "machine learning Rwanda hiring",
    '"hiring" AI engineer Africa remote EMEA',
    '"work from anywhere" AI startup careers',
    "African AI research lab internship 2025",
]

AFRICA_GEO = re.compile(
    r"(nigeria|lagos|abuja|kenya|nairobi|south africa|"
    r"cape town|johannesburg|egypt|cairo|ghana|accra|"
    r"rwanda|kigali|uganda|kampala|senegal|dakar|"
    r"morocco|casablanca|tunisia|tunis|algeria|algiers|"
    r"ethiopia|addis ababa|ivory coast|abidjan)",
    re.IGNORECASE,
)

BLOCKED_DOMAINS = (
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "simplyhired.com", "myjobmag.com", "jobberman.com",
    "ngcareers.com", "hotnigerianjobs.com", "fuzu.com", "brightermonday.com",
    "careers24.com", "pnet.co.za", "recruitment", "github.com", "youtube.com",
)


async def main():
    from backbone.tools.tavily import SearchInput, TavilySearchTool
    from backbone.tools.base import ToolContext
    from career_copilot.config import get_settings

    # Load known names.
    raw = yaml.safe_load(WATCHLIST_PATH.open()) if WATCHLIST_PATH.exists() else {}
    companies = raw.get("companies", [])
    known = {c["name"].lower().strip() for c in companies}
    print(f"Known: {len(known)} companies. Queries: {len(DISCOVERY_QUERIES)}\n")

    ctx = ToolContext(agent="job_hunter", task_id=f"discover_{datetime.now(UTC).timestamp():.0f}", settings=get_settings())
    tool = TavilySearchTool()

    seen_domains: set[str] = set()
    candidates: list[tuple[str, str]] = []  # (domain, name_hint)

    for i, query in enumerate(DISCOVERY_QUERIES, 1):
        print(f"[{i:2d}] {query[:65]}...")
        try:
            out = await tool(ctx, SearchInput(query=query, max_results=3))
        except Exception as exc:
            print(f"     ! {exc}")
            continue
        for r in out.results:
            # Access Pydantic model attributes, not dict keys.
            url = r.url
            if not url:
                continue
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            if any(b in domain for b in BLOCKED_DOMAINS):
                continue
            combined = f"{r.title or ''} {r.content or ''} {url}".lower()
            if not AFRICA_GEO.search(combined):
                continue
            name_hint = domain.split(".")[0].replace("-", " ").title()
            candidates.append((domain, name_hint))
        print(f"     {len(out.results)} results, {len(candidates)} candidates so far")
        await asyncio.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"Found {len(candidates)} candidate companies\n")

    add_flag = "--add" in sys.argv
    new_entries: list[dict[str, Any]] = []
    for domain, name_hint in candidates:
        if name_hint.lower() in known:
            continue
        region = "nigeria" if ".ng" in domain else "africa"
        entry = {
            "name": name_hint,
            "region": region,
            "source_tier": 2,
            "careers_url": f"https://{domain}/careers",
        }
        new_entries.append(entry)
        print(f"  - name: \"{name_hint}\"")
        print(f"    region: \"{region}\"")
        print(f"    source_tier: 2")
        print(f"    careers_url: \"https://{domain}/careers\"")
        print()

    if new_entries and add_flag:
        companies.extend(new_entries)
        raw["companies"] = companies
        with open(WATCHLIST_PATH, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        print(f"Added {len(new_entries)} to watchlist ({WATCHLIST_PATH})")
    elif new_entries:
        print(f"Run with --add to append {len(new_entries)} to watchlist")
    else:
        print("No new companies found (all already known).")


if __name__ == "__main__":
    asyncio.run(main())