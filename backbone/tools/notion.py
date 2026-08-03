"""Notion API tool — save papers to Notion databases.

Creates/updates pages in the Papers and Professors Notion databases.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext


class CreatePageInput(BaseModel):
    """Input for notion.create_page."""

    database_id: str
    properties: dict[str, Any]


class CreatePageOutput(BaseModel):
    """Output for notion.create_page."""

    page_id: str
    url: str


class UpdatePageInput(BaseModel):
    """Input for notion.update_page."""

    page_id: str
    properties: dict[str, Any]


class UpdatePageOutput(BaseModel):
    """Output for notion.update_page."""

    success: bool


NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class CreatePageTool(Tool[CreatePageInput, CreatePageOutput]):
    """Create a page in a Notion database."""

    name = "notion.create_page"
    description = "Create a page in a Notion database (e.g. save a paper to the Papers DB)."
    input_schema = CreatePageInput
    output_schema = CreatePageOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: CreatePageInput) -> CreatePageOutput:
        settings = ctx.settings
        api_key = settings.notion_api_key

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        payload = {
            "parent": {"database_id": input.database_id},
            "properties": input.properties,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{NOTION_API}/pages", json=payload, headers=headers, timeout=30
            )
            resp.raise_for_status()

        data = resp.json()
        return CreatePageOutput(page_id=data["id"], url=data.get("url", ""))


class UpdatePageTool(Tool[UpdatePageInput, UpdatePageOutput]):
    """Update a Notion page's properties."""

    name = "notion.update_page"
    description = "Update properties on an existing Notion page."
    input_schema = UpdatePageInput
    output_schema = UpdatePageOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: UpdatePageInput) -> UpdatePageOutput:
        settings = ctx.settings
        api_key = settings.notion_api_key

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        payload = {"properties": input.properties}

        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{NOTION_API}/pages/{input.page_id}",
                json=payload,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()

        return UpdatePageOutput(success=True)


from backbone.tools.registry import register

register(CreatePageTool(), agent="paper_tracker")
register(UpdatePageTool(), agent="paper_tracker")
