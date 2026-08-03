"""Semantic Scholar API tool — free academic search with citation data.

Free tier: 100 req/sec with API key. Get key at:
    semanticscholar.org/product/api#api-key-form
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext

BASE = "https://api.semanticscholar.org/graph/v1"


# ── Models ──


class Author(BaseModel):
    author_id: str | None = None
    name: str

    model_config = {"coerce_numbers_to_str": False}


class PaperResult(BaseModel):
    paper_id: str
    title: str
    year: int | None = None
    citation_count: int = 0
    authors: list[Author] = []
    abstract: str = ""


class SearchInput(BaseModel):
    query: str
    year_start: int = 2025
    limit: int = 100
    sort: str = "citationCount:desc"


class SearchOutput(BaseModel):
    papers: list[PaperResult]
    total: int = 0


class BatchInput(BaseModel):
    paper_ids: list[str]


class BatchOutput(BaseModel):
    papers: list[PaperResult]


class AuthorInput(BaseModel):
    author_id: str


class AuthorInfo(BaseModel):
    author_id: str
    name: str
    affiliations: list[str] = []
    paper_count: int = 0
    citation_count: int = 0
    h_index: int = 0


# ── Tool ──


class SemanticScholarTool(Tool[SearchInput, SearchOutput]):
    name = "semantic_scholar.search"
    description = (
        "Search Semantic Scholar for highly-cited papers by topic, sorted by citation count."
    )
    input_schema = SearchInput
    output_schema = SearchOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: SearchInput) -> SearchOutput:
        return await search_papers(ctx, input)

    @staticmethod
    async def get_batch(ctx: ToolContext, paper_ids: list[str]) -> BatchOutput:
        return await batch_lookup(ctx, BatchInput(paper_ids=paper_ids))

    @staticmethod
    async def get_author(ctx: ToolContext, author_id: str) -> AuthorInfo | None:
        return await lookup_author(ctx, AuthorInput(author_id=author_id))


async def _get(ctx: ToolContext, path: str) -> dict[str, Any]:
    settings = ctx.settings
    url = f"{BASE}/{path}"
    headers: dict[str, str] = {}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    else:
        print("[s2] WARNING: No API key set in settings")

    async with httpx.AsyncClient() as client:
        resp = None
        for attempt in range(5):
            try:
                resp = await client.get(url, headers=headers, timeout=20)
            except httpx.RequestError:
                # Network blip — back off and retry.
                await asyncio.sleep(3 * (attempt + 1))
                continue
            if resp.status_code == 429:
                wait = 3 * (attempt + 1)
                print(f"[s2] 429 rate limited, waiting {wait}s...")
                await asyncio.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                # S2 periodically returns 500s during peak hours; back off and
                # retry rather than dropping the whole keyword immediately.
                wait = 3 * (attempt + 1)
                print(f"[s2] {resp.status_code}, retrying in {wait}s (attempt {attempt+1}/5)")
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        # After all retries, raise on the last response
        if resp is not None:
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        raise RuntimeError("S2 request failed after all retries")


async def search_papers(ctx: ToolContext, input: SearchInput) -> SearchOutput:
    fields = "paperId,title,year,citationCount,authors,abstract"
    encoded_query = quote(input.query)
    path = (
        f"paper/search?query={encoded_query}"
        f"&year={input.year_start}-"
        f"&limit={input.limit}"
        f"&fieldsOfStudy=Computer+Science"
        f"&sort={input.sort}"
        f"&fields={fields}"
    )
    data = await _get(ctx, path)
    papers = [
        PaperResult(
            paper_id=p.get("paperId", ""),
            title=p.get("title", ""),
            year=p.get("year"),
            citation_count=p.get("citationCount", 0),
            abstract=p.get("abstract", "") or "",
            authors=[
                Author(author_id=a.get("authorId") or None, name=a.get("name", "") or "")
                for a in p.get("authors", [])
            ],
        )
        for p in data.get("data", [])
    ]
    return SearchOutput(papers=papers, total=data.get("total", 0))


async def batch_lookup(ctx: ToolContext, input: BatchInput) -> BatchOutput:
    if not input.paper_ids:
        return BatchOutput(papers=[])
    fields = "paperId,title,year,citationCount,authors,abstract"
    path = f"paper/batch?fields={fields}"
    settings = ctx.settings
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    async with httpx.AsyncClient() as client:
        resp = None
        for attempt in range(4):
            try:
                resp = await client.post(
                    f"{BASE}/{path}",
                    json={"ids": input.paper_ids[:500]},
                    headers=headers,
                    timeout=15,
                )
            except httpx.RequestError:
                await asyncio.sleep(4 * (attempt + 1))
                continue
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"[s2] Batch 429, waiting {wait}s...")
                await asyncio.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                wait = 4 * (attempt + 1)
                print(f"[s2] Batch {resp.status_code}, retrying in {wait}s (attempt {attempt+1}/4)")
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            if resp is not None:
                resp.raise_for_status()
        if resp is None:
            raise RuntimeError("S2 batch lookup failed after all retries")
        data = resp.json()
    papers = []
    for p in data if isinstance(data, list) else data.get("data", []):
        if p is None:
            continue
        papers.append(
            PaperResult(
                paper_id=p.get("paperId", ""),
                title=p.get("title", ""),
                year=p.get("year"),
                citation_count=p.get("citationCount", 0),
                abstract=p.get("abstract", "") or "",
                authors=[
                    Author(author_id=a.get("authorId") or None, name=a.get("name", "") or "")
                    for a in p.get("authors", [])
                ],
            )
        )
    return BatchOutput(papers=papers)


async def lookup_author(ctx: ToolContext, input: AuthorInput) -> AuthorInfo | None:
    path = f"author/{input.author_id}?fields=name,affiliations,paperCount,citationCount,hIndex"
    try:
        data = await _get(ctx, path)
        return AuthorInfo(
            author_id=str(data.get("authorId", "")),
            name=data.get("name", ""),
            affiliations=data.get("affiliations", []),
            paper_count=data.get("paperCount", 0),
            citation_count=data.get("citationCount", 0),
            h_index=data.get("hIndex", 0),
        )
    except Exception:
        return None


# Auto-register
from backbone.tools.registry import register

register(SemanticScholarTool(), agent="paper_tracker")
