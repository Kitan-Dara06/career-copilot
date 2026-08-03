#!/usr/bin/env python3
"""Scheduled job worker — polls the ``scheduled_jobs`` table and runs due jobs.

This is what makes ``/digest on`` actually work. Without this worker, the
scheduled_jobs table accumulates rows that never execute.

Runs as a standalone process alongside the Telegram bot:
    uv run python -m career_copilot worker

Features:
- Polls every 30 seconds.
- Skips jobs that ran within the last 23h (prevents duplicate digests).
- Writes ``last_run_at`` to the DB so restarts don't re-spam.
- Lightweight: ~2 MB of RAM, ~0% CPU when idle.
- Handles both Paper Tracker and Job Hunter digests via the shared Dispatcher.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from backbone.db.session import async_session_factory
from backbone.dispatcher.dispatcher import Dispatcher
from backbone.dispatcher.wiring import wire_job_hunter, wire_paper_tracker
from career_copilot.config import configure_logging, get_settings

logger = logging.getLogger("worker")

POLL_INTERVAL_SEC = 30
MIN_GAP_BETWEEN_RUNS = timedelta(hours=23)  # don't re-run a digest too soon


async def _fetch_due_jobs() -> list[dict]:
    """Return scheduled_jobs rows that are enabled and due or never run."""
    factory = async_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                """SELECT id, job_id, job_name, cron_expression, payload, last_run_at
                   FROM scheduled_jobs
                   WHERE enabled = true
                     AND (last_run_at IS NULL
                          OR last_run_at < now() - :gap::interval)
                   ORDER BY last_run_at ASC NULLS FIRST
                   LIMIT 10"""
            ),
            {"gap": f"{int(MIN_GAP_BETWEEN_RUNS.total_seconds())} seconds"},
        )
        rows = result.fetchall()
        return [
            {
                "id": r.id,
                "job_id": r.job_id,
                "job_name": r.job_name,
                "cron_expression": r.cron_expression,
                "payload": r.payload or {},
                "last_run_at": r.last_run_at,
            }
            for r in rows
        ]


async def _mark_run(job_id: str) -> None:
    """Update last_run_at for a completed job."""
    factory = async_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE scheduled_jobs SET last_run_at = now() WHERE job_id = :jid"
            ),
            {"jid": job_id},
        )
        await session.commit()


async def _process_one(dispatcher: Dispatcher, job: dict) -> None:
    """Dispatch a single scheduled job to the appropriate agent."""
    payload = job.get("payload") or {}
    command = payload.get("command", job.get("job_name", "digest"))
    args = payload.get("args", ["now"])
    logger.info("worker_processing", job_name=job["job_name"], job_id=job["job_id"])
    try:
        result = await dispatcher.handle_command(
            user_id="aaliyah",  # scheduled jobs are user-agnostic
            command=command,
            args=args,
        )
        if result.success:
            logger.info("worker_done", job_name=job["job_name"])
        else:
            logger.warning("worker_failed", job_name=job["job_name"], error=result.error)
    except Exception:
        logger.exception("worker_crash", job_name=job["job_name"])
    finally:
        await _mark_run(job["job_id"])


async def run_worker() -> None:
    """Main worker loop — polls every POLL_INTERVAL_SEC."""
    from backbone.observability import setup_telemetry
    setup_telemetry(service_name="career-copilot-worker", service_version="0.2.0")
    configure_logging(json_output=False)
    settings = get_settings()

    # Build dispatcher with both agents wired (same as app.py).
    dispatcher = Dispatcher()
    wire_paper_tracker(dispatcher)
    wire_job_hunter(dispatcher)

    logger.info("worker_starting", poll_interval=POLL_INTERVAL_SEC)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError: pass
    while not stop_event.is_set():
        try:
            due = await _fetch_due_jobs()
            if due:
                logger.info("worker_due_jobs", count=len(due))
                for job in due:
                    await _process_one(dispatcher, job)
            await asyncio.wait([stop_event.wait()], timeout=POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            break
        except Exception:
            logger.exception("worker_loop_error")
            await asyncio.sleep(5)

    logger.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run_worker())
