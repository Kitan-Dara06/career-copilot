"""GitHub tools — search issues + fetch issue details for Contribution Finder.

Two tools:
  - github.search_issues — Search GitHub Issues API by query string.
  - github.fetch_issue — Get full issue details by owner/repo/number.

Both work unauthenticated (60 req/h) or with GITHUB_TOKEN env var (5000 req/h).
No GitHub OAuth required — fine-grained PAT with Issues: Read-only is sufficient.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext

GITHUB_API = "https://api.github.com"

# ── Models ───────────────────────────────────────────────────────


class SearchIssuesInput(BaseModel):
    """Input for github.search_issues."""

    query: str
    sort: str = "created"  # created | updated | comments | reactions-+1
    order: str = "desc"    # desc | asc
    per_page: int = 30


class GitHubIssue(BaseModel):
    """A single GitHub issue with the fields we need for scoring."""

    repo_full_name: str     # "owner/repo"
    issue_number: int
    title: str
    body: str = ""
    url: str
    labels: list[str] = []
    state: str = "open"
    created_at: str = ""    # ISO 8601
    updated_at: str = ""    # ISO 8601
    comment_count: int = 0
    reaction_count: int = 0
    linked_pr_count: int = 0


class SearchIssuesOutput(BaseModel):
    """Output for github.search_issues."""

    issues: list[GitHubIssue]
    total_count: int
    query: str


class FetchIssueInput(BaseModel):
    """Input for github.fetch_issue."""

    repo_full_name: str   # "owner/repo"
    issue_number: int


class FetchIssueOutput(BaseModel):
    """Output for github.fetch_issue."""

    issue: GitHubIssue


# ── Helpers ───────────────────────────────────────────────────────


def _headers() -> dict[str, str]:
    h: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "career-copilot-contribution-finder",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """GET with rate-limit handling. Returns parsed JSON or raises."""
    for attempt in range(3):
        resp = await client.get(url, headers=_headers(), timeout=15)
        if resp.status_code == 403 and "rate limit" in resp.text.lower() and attempt < 2:
            await asyncio.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("GitHub rate limit exhausted after 3 attempts")


def _parse_issue(item: dict[str, Any]) -> GitHubIssue:
    """Parse a GitHub API issue/pull-request dict into our model."""
    labels = [lbl.get("name", "") for lbl in (item.get("labels") or [])]
    # Count reactions across all types (GitHub returns ints, but some
    # fields like '+1' can be string keys — sum with int conversion).
    reactions = item.get("reactions", {}) or {}
    reaction_count = sum(int(v) for v in reactions.values() if str(v).isdigit())
    # Linked PR count: issues with pull_request key have a linked PR
    linked = 1 if item.get("pull_request") else 0
    url_parts = (item.get("repository_url") or "").split("/")
    repo = "/".join(url_parts[-2:]) if len(url_parts) >= 2 else ""
    return GitHubIssue(
        repo_full_name=repo or item.get("repository", {}).get("full_name", ""),
        issue_number=item.get("number", 0),
        title=item.get("title", ""),
        body=(item.get("body") or "")[:4000],
        url=item.get("html_url", ""),
        labels=labels,
        state=item.get("state", "open"),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
        comment_count=item.get("comments", 0),
        reaction_count=reaction_count,
        linked_pr_count=linked,
    )


# ── Tools ─────────────────────────────────────────────────────────


class SearchIssuesTool(Tool[SearchIssuesInput, SearchIssuesOutput]):
    """Search GitHub Issues API with a query string."""

    name = "github.search_issues"
    description = "Search open GitHub issues by query. Supports language:python, label:X, is:issue, is:open."
    input_schema = SearchIssuesInput
    output_schema = SearchIssuesOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "contribution_finder"

    async def __call__(self, ctx: ToolContext, input: SearchIssuesInput) -> SearchIssuesOutput:
        url = f"{GITHUB_API}/search/issues"
        params = {
            "q": input.query,
            "sort": input.sort,
            "order": input.order,
            "per_page": str(input.per_page),
        }
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        async with httpx.AsyncClient() as client:
            data = await _get_json(client, f"{url}?{qs}")
        issues = [_parse_issue(item) for item in (data.get("items") or [])]
        return SearchIssuesOutput(
            issues=issues,
            total_count=data.get("total_count", 0),
            query=input.query,
        )


class FetchIssueTool(Tool[FetchIssueInput, FetchIssueOutput]):
    """Fetch full details of a single GitHub issue by owner/repo/number."""

    name = "github.fetch_issue"
    description = "Get full issue details including body, labels, reactions, and linked PRs."
    input_schema = FetchIssueInput
    output_schema = FetchIssueOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "contribution_finder"

    async def __call__(self, ctx: ToolContext, input: FetchIssueInput) -> FetchIssueOutput:
        url = f"{GITHUB_API}/repos/{input.repo_full_name}/issues/{input.issue_number}"
        async with httpx.AsyncClient() as client:
            data = await _get_json(client, url)
        return FetchIssueOutput(issue=_parse_issue(data))


# Auto-register.
from backbone.tools.registry import register

register(SearchIssuesTool(), agent="contribution_finder")
register(FetchIssueTool(), agent="contribution_finder")