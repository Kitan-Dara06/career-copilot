"""Career Copilot MCP server — stdio entry point.

Exposes a curated, read-only subset of Career Copilot data to Hermes Agent.

Run standalone for debugging:

    uv run python -m backbone.mcp.server

Hermes launches this same command via its MCP config (see deploy/hermes/).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .adapters import load_profile
from .policy import apply_policy

mcp = FastMCP("career-copilot")


@mcp.tool(name="career.profile.get")
def career_profile_get() -> dict:
    """Get the user's canonical profile.

    Returns research interests, keywords, arXiv categories, preferences,
    and the weighted skill clusters used for matching across agents.
    Read-only; never writes.
    """
    return apply_policy(load_profile())


if __name__ == "__main__":
    mcp.run()
