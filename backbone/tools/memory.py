"""Memory feedback tool — record user engagement signals.

Writes to the ``feedback_log`` table. Used by inline Telegram buttons
(Read, Save, Skip, More like this, Less like this).
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel

from backbone.db.session import async_session_factory
from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext

logger = structlog.get_logger("tools.feedback")


class FeedbackSignal(BaseModel):
    """A single user feedback signal."""

    item_id: str
    signal: str  # "read" | "save" | "skip" | "more" | "less"
    stream: str | None = None  # "interest" | "professor"


class FeedbackInput(BaseModel):
    """Input for memory.feedback."""

    item_id: str
    signal: str
    stream: str | None = None


class FeedbackOutput(BaseModel):
    """Output for memory.feedback."""

    success: bool


class FeedbackTool(Tool[FeedbackInput, FeedbackOutput]):
    """Record a user engagement signal in the feedback log."""

    name = "memory.feedback"
    description = "Record a user feedback signal (read/save/skip/more/less) on a digest item."
    input_schema = FeedbackInput
    output_schema = FeedbackOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: FeedbackInput) -> FeedbackOutput:
        from sqlalchemy import text

        factory = async_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO feedback_log (item_id, signal, stream)
                    VALUES (:item_id, :signal, :stream)
                    """
                ),
                {
                    "item_id": input.item_id,
                    "signal": input.signal,
                    "stream": input.stream,
                },
            )
            await session.commit()

        logger.info("feedback_recorded", item_id=input.item_id, signal=input.signal)
        return FeedbackOutput(success=True)


from backbone.tools.registry import register

register(FeedbackTool(), agent="paper_tracker")
