"""Probe: capture raw Gemini response for professor_verify on a known-good faculty page.

This isolates what the LLM actually returns when we hand it markdown from a real
professor homepage, so we can fix the JSON parsing before re-running /discover.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backbone.tools.base import ToolContext  # noqa: E402
from backbone.tools.firecrawl import FirecrawlScrapeTool, ScrapeInput  # noqa: E402
from agents.paper_tracker.agent import PaperTrackerAgent  # noqa: E402
from career_copilot.config import get_settings  # noqa: E402

# A genuinely stable faculty homepage where "Professor" appears verbatim.
PROBES = [
    ("Dawei Yin", "https://www.yindawei.com"),
    ("Hao Peng", "https://sites.google.com/view/haopeng/"),
    ("Jimmy Lin", "https://jimmylin.org/"),
]


async def main() -> None:
    settings = get_settings()
    ctx = ToolContext(agent="paper_tracker", task_id="verify-probe", settings=settings)
    agent = PaperTrackerAgent(task_ctx=ctx)
    firecrawl = FirecrawlScrapeTool()

    for name, url in PROBES:
        print("=" * 80)
        print(f"Probe: {name}  {url}")
        try:
            scrape = await firecrawl(ctx, ScrapeInput(url=url, formats=["markdown"]))
            md = (scrape.content.markdown or "")[:8000]
            print(f"  markdown length: {len(md)}")
            print(f"  first 200 chars: {md[:200]!r}")
            if "professor" in md.lower():
                idx = md.lower().find("professor")
                print(f"  found 'professor' at idx={idx}: {md[max(0,idx-40):idx+80]!r}")
            elif "prof" in md.lower():
                idx = md.lower().find("prof")
                print(f"  (only 'prof' substring) idx={idx}: {md[max(0,idx-30):idx+80]!r}")
            else:
                print("  WARNING: 'professor' / 'prof' not found in page text")
        except Exception as exc:
            print(f"  firecrawl FAILED: {exc}")
            continue

        # Now invoke the LLM verify path directly.
        template_md = md  # pass the same markdown we scraped
        try:
            out = await agent._llm_verify_professor(name, template_md)
            print(f"  parsed verify dict: {out}")
        except Exception as exc:
            print(f"  _llm_verify_professor RAISED: {exc}")

        # Now also capture the raw model output so we can see why JSON parsing
        # failed (if it does).
        from backbone.prompt_registry.loader import load as load_prompt, render

        template = load_prompt("paper_tracker", "professor_verify")
        rendered, _ = render(
            template,
            {"prof_name": name, "homepage_markdown": template_md or "(empty)"},
        )
        schema = {
            "type": "object",
            "properties": {
                "is_professor": {"type": "boolean"},
                "position": {"type": "string"},
                "department": {"type": "string"},
                "university": {"type": "string"},
                "country": {"type": "string"},
            },
            "required": ["is_professor", "position", "department", "university", "country"],
        }
        raw = await agent._llm.generate(
            model=template.model.name,
            prompt=rendered,
            temperature=template.model.temperature,
            max_tokens=template.model.max_tokens,
            response_format="json",
            response_schema=schema,
        )
        print(f"  raw output length: {len(raw) if raw else 0}")
        print(f"  raw output: {raw!r}")


if __name__ == "__main__":
    asyncio.run(main())