"""Firecrawl scraping tool — deep page scrape for JS-heavy lab pages.

Used for professor homepage and lab page scraping in the /prof and /discover flows.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext


class ScrapeInput(BaseModel):
    """Input for firecrawl.scrape."""

    url: str
    formats: list[str] = ["markdown"]
    wait_for: int = 3000  # ms to wait for JS rendering (React SPAs)


class ScrapedContent(BaseModel):
    """Scraped page content."""

    url: str
    markdown: str
    title: str | None = None


class ScrapeOutput(BaseModel):
    """Output for firecrawl.scrape."""

    content: ScrapedContent


class FirecrawlScrapeTool(Tool[ScrapeInput, ScrapeOutput]):
    """Deep scrape a web page (handles JS-heavy pages)."""

    name = "firecrawl.scrape"
    description = "Deep scrape a web page and return clean markdown. Handles JS-heavy pages."
    input_schema = ScrapeInput
    output_schema = ScrapeOutput
    cost_hint = CostHint.EXTERNAL_API_CALL
    latency_hint = LatencyHint.AROUND_30S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: ScrapeInput) -> ScrapeOutput:
        settings = ctx.settings
        api_key = settings.firecrawl_api_key

        url = "https://api.firecrawl.dev/v1/scrape"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"url": input.url, "formats": input.formats, "waitFor": input.wait_for}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()

        data = resp.json()
        return ScrapeOutput(
            content=ScrapedContent(
                url=input.url,
                markdown=data.get("data", {}).get("markdown", ""),
                title=data.get("data", {}).get("metadata", {}).get("title"),
            )
        )


from backbone.tools.registry import register

register(FirecrawlScrapeTool(), agent="paper_tracker")
