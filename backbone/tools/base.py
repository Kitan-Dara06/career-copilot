"""Tool base classes — the Tool ABC, ToolContext, and enums.

Every tool in the registry subclasses ``Tool`` and receives a ``ToolContext``
carrying agent identity, memory access, the prompt logger, and settings.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

TIn = TypeVar("TIn", bound=BaseModel)
TOut = TypeVar("TOut", bound=BaseModel)


class CostHint(StrEnum):
    """Rough cost tier for a single tool invocation."""

    FREE = "free"
    ONE_LLM_CALL = "1 LLM call"
    EXTERNAL_API_CALL = "external API call"


class LatencyHint(StrEnum):
    """Rough latency tier for a single tool invocation."""

    FAST = "fast"
    AROUND_3S = "~3s"
    AROUND_30S = "~30s"


class ToolContext(BaseModel):
    """Context injected into every tool call by the agent runtime.

    Attributes:
        agent: The agent name (e.g. ``"paper_tracker"``).
        task_id: Unique task identifier.
        settings: Application settings (API keys, DB URLs, etc.).
    """

    model_config = {"arbitrary_types_allowed": True}

    agent: str
    task_id: str
    # Lazy imports to avoid circular dependencies:
    # memory and prompt_logger are injected as Any until Phase 5.
    settings: Any
    memory: Any = None
    prompt_logger: Any = None


class Tool(ABC, Generic[TIn, TOut]):
    """Abstract base for every tool in the registry.

    Subclasses must define: ``name``, ``description``, ``input_schema``,
    ``output_schema``, ``cost_hint``, ``latency_hint``, ``owner``, and
    implement ``__call__``.

    TIn: Type variable for tool input (must subclass BaseModel).
    TOut: Type variable for tool output (must subclass BaseModel).
    """

    name: str
    description: str
    input_schema: type[TIn]
    output_schema: type[TOut]
    cost_hint: CostHint
    latency_hint: LatencyHint
    owner: str  # agent name, e.g. "paper_tracker"

    @abstractmethod
    async def __call__(self, ctx: ToolContext, input: TIn) -> TOut:
        """Execute the tool with the given context and input."""
        ...
