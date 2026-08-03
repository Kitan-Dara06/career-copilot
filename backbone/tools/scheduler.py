"""Scheduler tool — persist and poll scheduled jobs.

Jobs are stored in the ``scheduled_jobs`` table. A background worker
(Phase 6) polls every 30s and triggers the dispatcher for due jobs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import text

from backbone.db.session import async_session_factory
from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext


class ScheduleInput(BaseModel):
    """Input for scheduler.schedule."""

    job_name: str
    cron_expression: str
    payload: dict[str, object] = {}


class ScheduleOutput(BaseModel):
    """Output for scheduler.schedule."""

    job_id: str


class ScheduleTool(Tool[ScheduleInput, ScheduleOutput]):
    """Persist a scheduled job to the scheduled_jobs table."""

    name = "scheduler.schedule"
    description = "Schedule a recurring job with a cron expression."
    input_schema = ScheduleInput
    output_schema = ScheduleOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: ScheduleInput) -> ScheduleOutput:
        from json import dumps

        factory = async_session_factory()
        job_id = f"job_{input.job_name}_{int(datetime.now(UTC).timestamp())}"

        async with factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO scheduled_jobs
                      (job_id, job_name, cron_expression, payload, created_at, enabled)
                    VALUES
                      (:job_id, :job_name, :cron, :payload::jsonb, :now, true)
                    """
                ),
                {
                    "job_id": job_id,
                    "job_name": input.job_name,
                    "cron": input.cron_expression,
                    "payload": dumps(input.payload),
                    "now": datetime.now(UTC),
                },
            )
            await session.commit()

        return ScheduleOutput(job_id=job_id)


from backbone.tools.registry import register

register(ScheduleTool(), agent="paper_tracker")
