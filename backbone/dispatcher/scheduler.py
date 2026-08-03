"""Scheduled task worker — polls the ``scheduled_jobs`` table and fires due jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC

from sqlalchemy import text

from backbone.db.session import async_session_factory

logger = logging.getLogger("dispatcher.scheduler")


class ScheduledTaskWorker:
    """Background worker that polls ``scheduled_jobs`` every 30s."""

    def __init__(
        self,
        on_due: Callable[[str], Awaitable[None]],
    ) -> None:
        """Initialise the worker.

        Args:
            on_due: Async callable ``(job_id: str) -> None``.
        """
        self._on_due = on_due
        self._running = False
        self._factory = async_session_factory()

    async def start(self) -> None:
        """Start the polling loop (runs indefinitely)."""
        self._running = True
        logger.info("scheduler_worker_started")
        while self._running:
            try:
                await self._poll()
            except Exception:
                logger.exception("scheduler_poll_error")
            await asyncio.sleep(30)

    async def stop(self) -> None:
        """Gracefully stop the polling loop."""
        self._running = False
        logger.info("scheduler_worker_stopped")

    async def _poll(self) -> None:
        """Fetch enabled jobs and fire any that are due."""
        async with self._factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT job_id, job_name, cron_expression, last_run_at
                    FROM scheduled_jobs
                    WHERE enabled = true
                    """
                ),
            )
            rows = result.all()

        for row in rows:
            if row.last_run_at is None:
                # Fire the job and record the run
                try:
                    if self._on_due is not None:
                        await self._on_due(str(row.job_id))
                except Exception:
                    logger.exception("job_fire_failed", extra={"job_id": row.job_id})
                await self._record_run(str(row.job_id))

    async def _record_run(self, job_id: str) -> None:
        """Update last_run_at for a job."""
        from datetime import datetime

        async with self._factory() as session:
            await session.execute(
                text("UPDATE scheduled_jobs SET last_run_at = :now WHERE job_id = :job_id"),
                {"now": datetime.now(UTC), "job_id": job_id},
            )
            await session.commit()
