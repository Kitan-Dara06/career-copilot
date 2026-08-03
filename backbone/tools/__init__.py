"""Tool registry — all tools available to agents.

Importing this package auto-registers every tool via the ``register()``
calls at the bottom of each tool module.
"""

from __future__ import annotations

# Import all tool modules to trigger auto-registration
from . import (
    arxiv,  # noqa: F401
    email,  # noqa: F401
    firecrawl,  # noqa: F401
    github,  # noqa: F401
    http,  # noqa: F401
    memory,  # noqa: F401
    notion,  # noqa: F401
    scheduler,  # noqa: F401
    semantic_scholar,  # noqa: F401
    structured,  # noqa: F401
    tavily,  # noqa: F401
    telegram,  # noqa: F401
    vector,  # noqa: F401
)
from .base import CostHint, LatencyHint, Tool, ToolContext
from .registry import clear, get, list_for_agent, register, schemas_for_llm

__all__ = [
    "CostHint",
    "LatencyHint",
    "Tool",
    "ToolContext",
    "clear",
    "get",
    "list_for_agent",
    "register",
    "schemas_for_llm",
]
