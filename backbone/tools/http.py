"""HTTP fetch tool — generic URL fetch with optional short-term cache.

Used for one-off HTTP gets (arXiv OAI-PMH, Semantic Scholar, etc.).
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext


class FetchInput(BaseModel):
    """Input for http.fetch."""

    url: str
    cache_ttl_minutes: int | None = None


class FetchOutput(BaseModel):
    """Output for http.fetch."""

    status_code: int
    body: str
    headers: dict[str, str]


class FetchTool(Tool[FetchInput, FetchOutput]):
    """Generic HTTP GET with optional cache."""

    name = "http.fetch"
    description = "Fetch a URL and return the response body. Optional short-term caching."
    input_schema = FetchInput
    output_schema = FetchOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: FetchInput) -> FetchOutput:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(input.url, timeout=30)
            return FetchOutput(
                status_code=resp.status_code,
                body=resp.text,
                headers=dict(resp.headers),
            )


from backbone.tools.registry import register

register(FetchTool(), agent="paper_tracker")
