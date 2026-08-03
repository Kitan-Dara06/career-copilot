"""Job posting source tools — Greenhouse, Lever, Ashby, Firecrawl careers pages, Tavily search.

Three independent JournalPosting fetchers matching §13 of the job-hunter-design (1).md:
  - Tier 1: ATS APIs (Greenhouse, Lever, Ashby) — clean JSON, no auth.
  - Tier 2: Firecrawl careers-page scrape + LLM extraction prompt.
  - Tier 3: Tavily search by company name; one result, then Firecrawl or LLM extract.

Each fetcher returns a normalised list[JobPostingRecord] — the agent owns
match-score math, dedup, and persistence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext
from backbone.tools.firecrawl import FirecrawlScrapeTool, ScrapeInput
from backbone.tools.tavily import SearchInput, TavilySearchTool

# ── Models ───────────────────────────────────────────────────────


class JobPostingRecord(BaseModel):
    """Normalised posting record returned by every Tier fetcher."""

    external_id: str
    source: str  # 'greenhouse' | 'lever' | 'ashby' | 'firecrawl' | 'tavily'
    source_url: str
    title: str
    organization: str
    description: str = ""
    location: str | None = None
    remote_ok: bool | None = None
    role_type: str = "unknown"
    posted_at: datetime | None = None
    application_url: str | None = None
    raw_html: str | None = None
    # salary_* and visa_status are filled by the agent, not the fetcher.


class FetchATSInput(BaseModel):
    """Input for jobs.fetch_ats (Tier 1)."""

    ats: str  # 'greenhouse' | 'lever' | 'ashby'
    company_id: str
    organization: str  # display name for the UI


class FetchATSOutput(BaseModel):
    postings: list[JobPostingRecord]
    ats: str
    company_id: str


class FetchCareersPageInput(BaseModel):
    """Input for jobs.fetch_careers_page (Tier 2)."""

    careers_url: str
    organization: str


class FetchCareersPageOutput(BaseModel):
    postings: list[JobPostingRecord]
    careers_url: str


class FetchViaTavilyInput(BaseModel):
    """Input for jobs.fetch_via_tavily (Tier 3)."""

    tavily_query: str
    organization: str


class FetchViaTavilyOutput(BaseModel):
    postings: list[JobPostingRecord]
    query: str


# ── ATS base URLs (all public, no auth) ─────────────────────────────

GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards"
LEVER_BASE = "https://api.lever.co/v0/postings"
ASHBY_BASE = "https://api.ashbyhq.com/posting-api/job-board"


# ── Tier 1 fetchers ────────────────────────────────────────────────


def _classify_role_type(title: str) -> str:
    """Cheap title-based role classification."""
    t = (title or "").lower()
    if any(k in t for k in ["phd", "intern", "internship", "research intern"]):
        if "research" in t or "phd" in t:
            return "research"
        return "internship"
    if any(k in t for k in ["co-op", "coop", "co op", "new grad", "new_grad", "graduate", "entry level", "entry-level"]):
        return "new_grad"
    if "senior" in t or "staff" in t or "principal" in t:
        return "experienced"
    return "unknown"


def _classify_context(title: str, org: str = "") -> str:
    """Classify a posting as 'academic' (university lab, PhD track) or 'industry'.

    Used by F-JH.2 for routing to different research-block builders.
    Academic: PhD position, postdoc, research assistant in a university.
    Industry: company internship, new-grad role, applied research at a company.
    Returns 'academic' or 'industry'.
    """
    t = (title or "").lower()
    o = (org or "").lower()

    academic_signals = (
        "phd", "doctoral", "postdoc", "postdoctoral", "dissertation",
        "graduate research", "research assistant", "graduate assistantship",
        "master's thesis", "masters thesis", "m.sc.", "m.phil",
        "university of", "institute of", "faculty of", "department of",
        "professor", "lecturer", "tenure track",
    )
    if any(s in t for s in academic_signals):
        return "academic"
    # University-affiliated orgs
    if any(s in o for s in ("university", "institute", "college", "school of", "eth", "epfl", "mit", "stanford")):
        if any(s in t for s in ("research", "phd", "postdoc", "assistant")):
            return "academic"

    return "industry"


def _normalise_posted_at(value: str | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Lever sometimes returns epoch seconds, Greenhouse sometimes
        # returns epoch milliseconds.
        try:
            seconds = value / 1000.0 if value > 1e10 else value
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (ValueError, OSError):
            return None
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _greenhouse_fetch(client: httpx.AsyncClient, company_id: str, org: str) -> list[JobPostingRecord]:
    url = f"{GREENHOUSE_BASE}/{company_id}/jobs?content=true"
    resp = await client.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    jobs = data.get("jobs", [])
    out: list[JobPostingRecord] = []
    for j in jobs:
        ext = f"gh:{j.get('id')}"
        title = j.get("title", "") or "(untitled)"
        job_url = (
            j.get("absolute_url")
            or f"https://boards.greenhouse.io/{company_id}/jobs/{j.get('id')}"
        )
        # Greenhouse puts the human-readable description under "content"; the
        # text version under "metadata" sometimes too. We keep `content` if
        # present, fall back to concatenating metadata.
        desc = j.get("content", "") or ""
        if not desc:
            metadata = j.get("metadata", "") or ""
            if metadata:
                desc = str(metadata)
        # Strip HTML tags cheaply for the embed/match pipeline.
        if desc:
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip()[:4000]
        location = (j.get("location", {}) or {}).get("name") if isinstance(j.get("location"), dict) else None
        # remote_ok detection is heuristic at this layer; Greenhouse's metadata
        # has "Remote" tokens. Default None — agent infers from text.
        out.append(
            JobPostingRecord(
                external_id=ext,
                source="greenhouse",
                source_url=job_url,
                title=title,
                organization=org,
                description=desc,
                location=location,
                role_type=_classify_role_type(title),
                posted_at=_normalise_posted_at(j.get("updated_at") or j.get("first_published")),
                application_url=job_url,
            )
        )
    return out


async def _lever_fetch(client: httpx.AsyncClient, company_id: str, org: str) -> list[JobPostingRecord]:
    url = f"{LEVER_BASE}/{company_id}?mode=json"
    resp = await client.get(url, timeout=15)
    resp.raise_for_status()
    postings = resp.json()
    # Lever returns an array directly.
    if isinstance(postings, dict) and "data" in postings:
        postings = postings["data"]
    out: list[JobPostingRecord] = []
    for p in postings or []:
        ext = f"lever:{p.get('id')}"
        title = p.get("text", "") or "(untitled)"
        host = p.get("hostedUrl") or p.get("applyUrl", "")
        # Lever descriptions are HTML.
        desc = p.get("description", "") or ""
        if desc:
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip()[:4000]
        # Lever "categories" carries location, team, commitment.
        categories = p.get("categories", {}) or {}
        location = categories.get("location", "") or None
        commitment = categories.get("commitment", "") or None
        team = categories.get("team", "") or None
        if commitment and "remote" in commitment.lower():
            pass  # `remote_ok` is filled at agent layer from text+commitment
        create_time = p.get("createdAt") or p.get("createdAtAt")
        posted = _normalise_posted_at(create_time)
        # Description also lists plain-text descriptionPlain as a sister field.
        plain = p.get("descriptionPlain", "") or ""
        if plain and len(desc) < 200:
            desc = (plain[:4000])
        out.append(
            JobPostingRecord(
                external_id=ext,
                source="lever",
                source_url=host or "",
                title=title,
                organization=org,
                description=desc,
                location=location,
                role_type=_classify_role_type(title),
                posted_at=posted,
                application_url=host or "",
            )
        )
    return out


async def _ashby_fetch(client: httpx.AsyncClient, company_id: str, org: str) -> list[JobPostingRecord]:
    url = f"{ASHBY_BASE}/{company_id}"
    resp = await client.get(url, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    jobs = payload.get("postings", [])
    out: list[JobPostingRecord] = []
    for j in jobs:
        ext = f"ashby:{j.get('id', j.get('externalId', ''))}"
        title = j.get("title", "") or "(untitled)"
        job_url = j.get("applyUrl") or j.get("url") or ""
        # Ashby descriptions are sometimes HTML; convert.
        desc = j.get("descriptionHtml") or j.get("description") or ""
        if desc and "<" in desc:
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip()[:4000]
        location_data = j.get("location", {}) or {}
        location = location_data.get("name") if isinstance(location_data, dict) else str(location_data or "")
        posted = _normalise_posted_at(j.get("publishedDate") or j.get("updatedAt"))
        out.append(
            JobPostingRecord(
                external_id=ext,
                source="ashby",
                source_url=job_url,
                title=title,
                organization=org,
                description=desc,
                location=location,
                role_type=_classify_role_type(title),
                posted_at=posted,
                application_url=job_url,
            )
        )
    return out


class FetchATSTool(Tool[FetchATSInput, FetchATSOutput]):
    name = "jobs.fetch_ats"
    description = "Fetch open postings from a public ATS API (Greenhouse / Lever / Ashby)."
    input_schema = FetchATSInput
    output_schema = FetchATSOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "job_hunter"

    async def __call__(self, ctx: ToolContext, input: FetchATSInput) -> FetchATSOutput:
        ats = input.ats.strip().lower()
        if ats not in {"greenhouse", "lever", "ashby"}:
            raise ValueError(f"Unknown ATS: {ats!r}")
        # Longer timeout for boards with 400+ postings (Anthropic, Databricks,
        # Stripe). Greenhouse `?content=true` payloads can exceed 2 MB.
        async with httpx.AsyncClient(timeout=30) as client:
            rows: list[JobPostingRecord] = []
            for attempt in range(5):
                try:
                    if ats == "greenhouse":
                        rows = await _greenhouse_fetch(client, input.company_id, input.organization)
                    elif ats == "lever":
                        rows = await _lever_fetch(client, input.company_id, input.organization)
                    else:
                        rows = await _ashby_fetch(client, input.company_id, input.organization)
                    break
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code if exc.response is not None else 0
                    if code == 429 and attempt < 4:
                        wait = min(2 ** attempt * 5, 60)  # 5, 10, 20, 40, 60
                        await asyncio.sleep(wait)
                        continue
                    if 500 <= code < 600 and attempt < 4:
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    raise
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    if attempt < 4:
                        await asyncio.sleep(2 ** attempt)  # 1, 2, 4, 8, 16
                        continue
                    raise
        return FetchATSOutput(postings=rows, ats=ats, company_id=input.company_id)


# ── Tier 2 fetcher (Firecrawl careers page) ────────────────────────


class FetchCareersPageTool(Tool[FetchCareersPageInput, FetchCareersPageOutput]):
    name = "jobs.fetch_careers_page"
    description = "Scrape a careers page with Firecrawl, parse individual job rows with Gemini."
    input_schema = FetchCareersPageInput
    output_schema = FetchCareersPageOutput
    cost_hint = CostHint.EXTERNAL_API_CALL
    latency_hint = LatencyHint.AROUND_30S
    owner = "job_hunter"

    async def __call__(self, ctx: ToolContext, input: FetchCareersPageInput) -> FetchCareersPageOutput:
        firecrawl = FirecrawlScrapeTool()
        try:
            scrape_out = await firecrawl(
                ctx, ScrapeInput(url=input.careers_url, formats=["markdown"])
            )
            markdown = (scrape_out.content.markdown or "")[:6000]
            if not markdown or len(markdown) < 200:
                return FetchCareersPageOutput(postings=[], careers_url=input.careers_url)
        except Exception:
            return FetchCareersPageOutput(postings=[], careers_url=input.careers_url)

        # Parse individual job rows with Gemini 2.5 flash.
        from backbone.prompt_registry.loader import load as load_prompt
        from backbone.prompt_registry.loader import render
        from backbone.model_client import ModelClient, parse_loose_json

        try:
            template = load_prompt("job_hunter", "parse_careers_page")
            rendered, _ = render(
                template,
                {"company_name": input.organization, "page_content": markdown},
            )
            llm = ModelClient()
            raw = await llm.generate(
                model=template.model.name,
                prompt=rendered,
                temperature=template.model.temperature,
                max_tokens=template.model.max_tokens,
            )
            parsed = parse_loose_json(raw) if raw else None
            jobs = parsed.get("jobs", []) if isinstance(parsed, dict) else []
        except Exception:
            jobs = []

        # Build individual posting records for each extracted job row.
        postings: list[JobPostingRecord] = []
        seen_titles: set[str] = set()
        for j in jobs:
            title = (j.get("title") or "").strip()[:200]
            if not title or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            job_url = (j.get("url") or "").strip()
            if not job_url:
                job_url = input.careers_url
            location = (j.get("location") or "").strip()
            is_remote = bool(j.get("is_remote"))
            url_hash = hashlib.sha256(f"{input.careers_url}:{title}".encode()).hexdigest()[:12]
            postings.append(
                JobPostingRecord(
                    external_id=f"careers:{url_hash}",
                    source="firecrawl",
                    source_url=job_url,
                    title=title,
                    organization=input.organization,
                    description=f"{title} at {input.organization}. Location: {location or 'unspecified'}.",
                    location=location or None,
                    remote_ok=is_remote,
                    role_type="unknown",
                    posted_at=datetime.now(UTC),
                    application_url=job_url,
                )
            )

        # Fall back to one synthetic record if no rows were extractable.
        if not postings:
            url_hash = hashlib.sha256(input.careers_url.encode()).hexdigest()[:12]
            postings.append(
                JobPostingRecord(
                    external_id=f"careers:{url_hash}",
                    source="firecrawl",
                    source_url=input.careers_url,
                    title=f"{input.organization} -- careers page",
                    organization=input.organization,
                    description=markdown,
                    role_type="unknown",
                    posted_at=datetime.now(UTC),
                    application_url=input.careers_url,
                )
            )

        return FetchCareersPageOutput(postings=postings, careers_url=input.careers_url)


# ── Tier 3 fetcher (Tavily search) ─────────────────────────────────


class FetchViaTavilyTool(Tool[FetchViaTavilyInput, FetchViaTavilyOutput]):
    name = "jobs.fetch_via_tavily"
    description = "Search Tavily for company careers, scrape each result, extract posting data."
    input_schema = FetchViaTavilyInput
    output_schema = FetchViaTavilyOutput
    cost_hint = CostHint.EXTERNAL_API_CALL
    latency_hint = LatencyHint.AROUND_30S
    owner = "job_hunter"

    async def __call__(self, ctx: ToolContext, input: FetchViaTavilyInput) -> FetchViaTavilyOutput:
        tavily = TavilySearchTool()
        try:
            t_out = await tavily(
                ctx,
                SearchInput(query=input.tavily_query, max_results=3),
            )
        except Exception:
            return FetchViaTavilyOutput(postings=[], query=input.tavily_query)

        # Scrape each Tavily result URL individually and extract posting data.
        firecrawl = FirecrawlScrapeTool()
        from backbone.model_client import ModelClient, parse_loose_json

        postings: list[JobPostingRecord] = []
        seen_urls: set[str] = set()

        for r in (t_out.results or [])[:3]:
            url = r.url
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Scrape the individual job listing page.
            try:
                scrape_out = await firecrawl(
                    ctx, ScrapeInput(url=url, formats=["markdown"])
                )
                markdown = (scrape_out.content.markdown or "")[:4000]
            except Exception:
                markdown = ""

            if markdown and len(markdown) > 200:
                # Use parse_careers_page prompt to extract job rows.
                from backbone.prompt_registry.loader import load as load_prompt
                from backbone.prompt_registry.loader import render
                jobs: list[dict] = []
                try:
                    template = load_prompt("job_hunter", "parse_careers_page")
                    rendered, _ = render(
                        template,
                        {"company_name": input.organization, "page_content": markdown},
                    )
                    llm = ModelClient()
                    raw = await llm.generate(
                        model=template.model.name,
                        prompt=rendered,
                        temperature=template.model.temperature,
                        max_tokens=template.model.max_tokens,
                    )
                    parsed = parse_loose_json(raw) if raw else None
                    jobs = parsed.get("jobs", []) if isinstance(parsed, dict) else []
                except Exception:
                    pass

                if jobs:
                    for j in jobs:
                        title = (j.get("title") or "").strip()[:200]
                        if not title:
                            continue
                        job_url = (j.get("url") or "").strip() or url
                        loc = (j.get("location") or "").strip()
                        is_remote = bool(j.get("is_remote"))
                        ext_id = f"tavily:{hashlib.sha256(job_url.encode()).hexdigest()[:12]}"
                        postings.append(
                            JobPostingRecord(
                                external_id=ext_id,
                                source="tavily",
                                source_url=job_url,
                                title=title,
                                organization=input.organization,
                                description=f"{title} at {input.organization}. Location: {loc or 'unspecified'}.",
                                location=loc or None,
                                remote_ok=is_remote,
                                role_type="unknown",
                                posted_at=datetime.now(UTC),
                                application_url=job_url,
                            )
                        )
                    continue

            # Fall back to snippet-based synthetic record.
            external_id = f"tavily:{hashlib.sha256(url.encode()).hexdigest()[:12]}"
            postings.append(
                JobPostingRecord(
                    external_id=external_id,
                    source="tavily",
                    source_url=url,
                    title=f"{input.organization} -- careers (Tavily result)",
                    organization=input.organization,
                    description=(r.content or "")[:4000],
                    role_type="unknown",
                    posted_at=datetime.now(UTC),
                    application_url=url,
                )
            )

        return FetchViaTavilyOutput(postings=postings, query=input.tavily_query)


# Auto-register all three tools.
from backbone.tools.registry import register  # noqa: E402

register(FetchATSTool(), agent="job_hunter")
register(FetchCareersPageTool(), agent="job_hunter")
register(FetchViaTavilyTool(), agent="job_hunter")