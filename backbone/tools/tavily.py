"""Tavily web search tool — AI-native search for professor/lab research.

Used by Paper Tracker for `/discover` and `/prof` flows.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext


class SearchResult(BaseModel):
    """A single Tavily search result."""

    title: str
    url: str
    content: str
    score: float


class SearchInput(BaseModel):
    """Input for tavily.search."""

    query: str
    max_results: int = 5
    include_domains: list[str] | None = None


class SearchOutput(BaseModel):
    """Output for tavily.search."""

    results: list[SearchResult]


class ExtractInput(BaseModel):
    """Input for tavily.extract."""

    url: str


class ExtractContent(BaseModel):
    """Extracted content from a URL."""

    url: str
    raw_content: str


class ExtractOutput(BaseModel):
    """Output for tavily.extract."""

    content: ExtractContent


class TavilySearchTool(Tool[SearchInput, SearchOutput]):
    """AI-native web search via Tavily."""

    name = "tavily.search"
    description = (
        "Search the web using Tavily's AI-native search. Returns clean structured results."
    )
    input_schema = SearchInput
    output_schema = SearchOutput
    cost_hint = CostHint.EXTERNAL_API_CALL
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: SearchInput) -> SearchOutput:
        settings = ctx.settings
        api_key = settings.tavily_api_key

        url = "https://api.tavily.com/search"
        payload: dict[str, Any] = {
            "api_key": api_key,
            "query": input.query,
            "max_results": input.max_results,
        }
        if input.include_domains:
            payload["include_domains"] = input.include_domains

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            resp.raise_for_status()

        data = resp.json()
        results = data.get("results", [])
        return SearchOutput(
            results=[
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    content=r.get("content", ""),
                    score=r.get("score", 0.0),
                )
                for r in results
            ]
        )


class TavilyExtractTool(Tool[ExtractInput, ExtractOutput]):
    """Extract structured content from a URL via Tavily."""

    name = "tavily.extract"
    description = "Extract clean structured content from a URL using Tavily."
    input_schema = ExtractInput
    output_schema = ExtractOutput
    cost_hint = CostHint.EXTERNAL_API_CALL
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: ExtractInput) -> ExtractOutput:
        settings = ctx.settings
        api_key = settings.tavily_api_key

        url = "https://api.tavily.com/extract"
        payload = {"api_key": api_key, "urls": [input.url]}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            resp.raise_for_status()

        data = resp.json()
        extracted = data.get("results", [{}])[0]
        return ExtractOutput(
            content=ExtractContent(
                url=extracted.get("url", input.url),
                raw_content=extracted.get("raw_content", ""),
            )
        )


from backbone.tools.registry import register

register(TavilySearchTool(), agent="paper_tracker")
register(TavilyExtractTool(), agent="paper_tracker")
