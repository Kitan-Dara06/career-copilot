"""Email tool — queue draft emails for user approval, then send.

Emails are NEVER sent directly by an agent. The flow:
1. Agent calls ``email.queue_draft`` → writes to ``pending_drafts`` table
2. User receives a Telegram notification with the rendered draft
3. User approves → email listener calls ``email.send_now``
4. Draft marked as ``sent`` in DB
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from backbone.db.session import async_session_factory
from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext


class QueueDraftInput(BaseModel):
    """Input for email.queue_draft."""

    to: str
    subject: str
    body: str
    metadata: dict[str, Any] = {}


class QueueDraftOutput(BaseModel):
    """Output for email.queue_draft."""

    draft_id: str


class SendNowInput(BaseModel):
    """Input for email.send_now."""

    draft_id: str


class SendNowOutput(BaseModel):
    """Output for email.send_now."""

    success: bool


class QueueDraftTool(Tool[QueueDraftInput, QueueDraftOutput]):
    """Queue an email draft for user approval. Does NOT send."""

    name = "email.queue_draft"
    description = (
        "Queue an email draft for user approval. The email will NOT be sent until approved."
    )
    input_schema = QueueDraftInput
    output_schema = QueueDraftOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: QueueDraftInput) -> QueueDraftOutput:
        factory = async_session_factory()
        draft_id = f"draft_{datetime.now(UTC).timestamp()}"

        async with factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO pending_drafts
                      (draft_id, recipient, subject, body, metadata, status, created_at)
                    VALUES
                      (:draft_id, :to, :subject, :body, :metadata::jsonb, 'pending', :now)
                    """
                ),
                {
                    "draft_id": draft_id,
                    "to": input.to,
                    "subject": input.subject,
                    "body": input.body,
                    "metadata": json.dumps(input.metadata),
                    "now": datetime.now(UTC),
                },
            )
            await session.commit()

        return QueueDraftOutput(draft_id=draft_id)


class SendNowTool(Tool[SendNowInput, SendNowOutput]):
    """Send a previously queued draft email."""

    name = "email.send_now"
    description = (
        "Send a previously queued email draft by its ID. Only callable after user approval."
    )
    input_schema = SendNowInput
    output_schema = SendNowOutput
    cost_hint = CostHint.EXTERNAL_API_CALL
    latency_hint = LatencyHint.AROUND_3S
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: SendNowInput) -> SendNowOutput:
        factory = async_session_factory()

        async with factory() as session:
            # Fetch the draft
            result = await session.execute(
                text(
                    "SELECT * FROM pending_drafts WHERE draft_id = :draft_id AND status = 'pending'"
                ),
                {"draft_id": input.draft_id},
            )
            row = result.one_or_none()
            if row is None:
                raise ValueError(f"Draft {input.draft_id!r} not found or already sent")

            # Mark as sent (actual SMTP integration in v0.2)
            await session.execute(
                text(
                    "UPDATE pending_drafts"
                    " SET status = 'sent', sent_at = :now"
                    " WHERE draft_id = :draft_id"
                ),
                {
                    "now": datetime.now(UTC),
                    "draft_id": input.draft_id,
                },
            )
            await session.commit()

        return SendNowOutput(success=True)


from backbone.tools.registry import register

register(QueueDraftTool(), agent="paper_tracker")
register(SendNowTool(), agent="paper_tracker")
