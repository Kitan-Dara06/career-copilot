"""Probe: verify the OpenReview canonical-URL detection + Firecrawl works.

Test the new "site:openreview.net profile" Tavily lookup with two known
researchers who have an OpenReview profile (Yu Su, Boci Peng), and confirm the
URL detection + Firecrawl produces usable markdown before we burn a full
/discover run on it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.paper_tracker.agent import PaperTrackerAgent, OPENREVIEW_PROFILE_PREFIX  # noqa: E402
from backbone.tools.base import ToolContext  # noqa: E402
from backbone.tools.tavily import SearchInput  # noqa: E402
from backbone.tools.firecrawl import ScrapeInput  # noqa: E402
from career_copilot.config import get_settings  # noqa: E402

NAMES = ["Yu Su", "Boci Peng", "Wei Zou", "Sebastian Borgeaud"]


async def main() -> None:
    settings = get_settings()
    ctx = ToolContext(agent="paper_tracker", task_id="or-probe", settings=settings)
    agent = PaperTrackerAgent(task_ctx=ctx)

    for name in NAMES:
        print("=" * 60)
        print(f"Probe: {name}")
        try:
            or_out = await agent._tavily(
                ctx,
                SearchInput(
                    query=f'"{name}" site:openreview.net profile',
                    max_results=3,
                ),
            )
        except Exception as exc:
            print(f"  tavily FAILED: {exc}")
            continue

        profile_url = ""
        for r in or_out.results:
            print(f"  result url: {r.url}")
            print(f"  content (first 120 chars): {(r.content or '')[:120]!r}")
            if r.url.startswith(OPENREVIEW_PROFILE_PREFIX):
                profile_url = r.url
                break
        if not profile_url:
            print("  no OpenReview profile URL found")
            continue

        print(f"  ✔ canonical URL: {profile_url}")
        try:
            scrape = await agent._firecrawl(
                ctx,
                ScrapeInput(url=profile_url, formats=["markdown"]),
            )
            md = (scrape.content.markdown or "")[:2000]
            print(f"  firecrawl markdown length: {len(md)}")
            snippet = md[:400].replace("\n", " | ")
            print(f"  first 400 chars: {snippet!r}")
            if "affiliation" in md.lower() or "history" in md.lower():
                print("  ✔ contains affiliation/history keywords")
        except Exception as exc:
            print(f"  firecrawl FAILED: {exc}")


if __name__ == "__main__":
    asyncio.run(main())