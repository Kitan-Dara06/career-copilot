"""arXiv API tool — fetch recent papers and author publications.

Uses the public arXiv API (``https://export.arxiv.org/api/query``).
No API key required. Rate limit: 1 request per 3 seconds. No concurrency.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext

# ── Data models ──


class Paper(BaseModel):
    """A single arXiv paper."""

    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    published: datetime
    categories: list[str]
    pdf_url: str


class FetchRecentInput(BaseModel):
    """Input for arxiv.fetch_recent."""

    categories: list[str]
    since: datetime
    max_results: int = 50


class FetchRecentOutput(BaseModel):
    """Output for arxiv.fetch_recent."""

    papers: list[Paper]


class FetchAuthorInput(BaseModel):
    """Input for arxiv.fetch_author."""

    author_name: str
    since: datetime
    max_results: int = 20


class FetchAuthorOutput(BaseModel):
    """Output for arxiv.fetch_author."""

    papers: list[Paper]


# ── Helpers ──

ARXIV_API = "https://export.arxiv.org/api/query"
NS = "{http://www.w3.org/2005/Atom}"
_last_request: float = 0.0
_RATE_LIMIT_SECS = 3.0


def _parse_entry(entry: Any) -> Paper:
    """Parse an Atom entry into a Paper."""
    arxiv_id = entry.find(f"{NS}id").text.rsplit("/abs/", 1)[-1]
    title = entry.find(f"{NS}title").text.strip().replace("\n", " ")
    authors = [
        a.find(f"{NS}name").text
        for a in entry.findall(f"{NS}author")
        if a.find(f"{NS}name") is not None
    ]
    abstract = entry.find(f"{NS}summary").text.strip().replace("\n", " ")
    published = datetime.strptime(entry.find(f"{NS}published").text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    )
    categories = [c.get("term") for c in entry.findall(f"{NS}category") if c.get("term")]
    pdf_url = next(
        (link.get("href") for link in entry.findall(f"{NS}link") if link.get("title") == "pdf"),
        f"https://arxiv.org/pdf/{arxiv_id}",
    )
    return Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        abstract=abstract,
        published=published,
        categories=categories,
        pdf_url=pdf_url,
    )


async def _rate_limit() -> None:
    """Enforce arXiv rate limit: 1 request per 3 seconds."""
    global _last_request
    now = asyncio.get_event_loop().time()
    wait = _last_request + _RATE_LIMIT_SECS - now
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = now


# ── Tools ──


class FetchRecentTool(Tool[FetchRecentInput, FetchRecentOutput]):
    """Fetch recent arXiv papers by category since a given date."""

    name = "arxiv.fetch_recent"
    description = (
        "Fetch recent arXiv papers by category, filtered to those published since a given date."
    )
    input_schema = FetchRecentInput
    output_schema = FetchRecentOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: FetchRecentInput) -> FetchRecentOutput:
        categories_str = " OR ".join(f"cat:{c}" for c in input.categories)
        query = f"({categories_str})"
        url = (
            f"{ARXIV_API}?search_query={query}"
            f"&start=0&max_results={input.max_results}"
            f"&sortBy=submittedDate&sortOrder=descending"
        )

        async with httpx.AsyncClient() as client:
            await _rate_limit()
            for attempt in range(3):
                resp = await client.get(url, timeout=30)
                if resp.status_code == 429:
                    wait = 10 * (attempt + 1)
                    print(f"[arxiv] Rate limited, waiting {wait}s (attempt {attempt + 1}/3)...")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            else:
                resp.raise_for_status()

        root = ElementTree.fromstring(resp.text)
        papers = [_parse_entry(e) for e in root.findall(f"{NS}entry")]

        # Filter by date with a 2-day buffer (arXiv has weekend gaps)
        buffer = input.since - timedelta(days=2)
        papers = [p for p in papers if p.published >= buffer]

        return FetchRecentOutput(papers=papers)


class FetchAuthorTool(Tool[FetchAuthorInput, FetchAuthorOutput]):
    """Fetch recent papers by author name."""

    name = "arxiv.fetch_author"
    description = "Fetch recent arXiv papers by author name, published since a given date."
    input_schema = FetchAuthorInput
    output_schema = FetchAuthorOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: FetchAuthorInput) -> FetchAuthorOutput:
        # Clean author name for query
        name = re.sub(r"[^a-zA-Z\s]", "", input.author_name)
        name = " ".join(name.split())  # Normalize whitespace
        url = (
            f"{ARXIV_API}?search_query=au:{name}"
            f"&start=0&max_results={input.max_results}"
            f"&sortBy=submittedDate&sortOrder=descending"
        )

        async with httpx.AsyncClient() as client:
            await _rate_limit()
            for attempt in range(3):
                resp = await client.get(url, timeout=30)
                if resp.status_code == 429:
                    wait = 10 * (attempt + 1)
                    print(f"[arxiv] Rate limited, waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            else:
                resp.raise_for_status()

        root = ElementTree.fromstring(resp.text)
        papers = [_parse_entry(e) for e in root.findall(f"{NS}entry")]
        papers = [p for p in papers if p.published >= input.since - timedelta(days=2)]

        return FetchAuthorOutput(papers=papers)


class FetchByIdInput(BaseModel):
    """Input for arxiv.fetch_by_id."""
    arxiv_id: str

class FetchByIdOutput(BaseModel):
    """Output for arxiv.fetch_by_id."""
    paper: Paper | None

class FetchByIdTool(Tool[FetchByIdInput, FetchByIdOutput]):
    name = "arxiv.fetch_by_id"
    description = "Fetch a single arXiv paper by its ID (e.g. 2301.12345)."
    input_schema = FetchByIdInput
    output_schema = FetchByIdOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: FetchByIdInput) -> FetchByIdOutput:
        arxiv_id = input.arxiv_id.strip()
        parts = arxiv_id.split("v")
        if len(parts) > 1:
            arxiv_id = parts[0]
        url = f"{ARXIV_API}?id_list={arxiv_id}&max_results=1"
        async with httpx.AsyncClient() as client:
            for attempt in range(2):
                resp = await client.get(url, timeout=15)
                if resp.status_code == 429:
                    await asyncio.sleep(3)
                    continue
                break
            else:
                resp.raise_for_status()
        root = ElementTree.fromstring(resp.text)
        entries = root.findall(f"{NS}entry")
        if entries:
            return FetchByIdOutput(paper=_parse_entry(entries[0]))
        return FetchByIdOutput(paper=None)

# Auto-register
from backbone.tools.registry import register

register(FetchRecentTool(), agent="paper_tracker")
register(FetchAuthorTool(), agent="paper_tracker")
register(FetchByIdTool(), agent="paper_tracker")
