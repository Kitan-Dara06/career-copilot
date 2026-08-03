"""Dispatcher — routes user commands, callbacks, and scheduled tasks to the right agent runtime."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry.trace import Status, StatusCode

from .task import Task, TaskResult
from backbone.observability import (
    DISPATCHER_COMMAND, DISPATCHER_USER, DISPATCHER_AGENT,
    get_tracer,
)

logger = structlog.get_logger("dispatcher")


class Dispatcher:
    """Central router for all incoming tasks."""

    def __init__(self) -> None:
        self._agent_handlers: dict[str, Any] = {}
        self._tracer = get_tracer("dispatcher")

    def register_command(self, command: str, agent: str, handler: Any) -> None:
        """Register a command handler.

        Args:
            command: Command name without leading slash.
            agent: Agent name.
            handler: Async callable ``(task: Task) -> TaskResult``.
        """
        self._agent_handlers[command] = (agent, handler)
        logger.info("command_registered", command=command, agent=agent)

    async def handle_command(
        self, user_id: str, command: str, args: list[str] | None = None
    ) -> TaskResult:
        """Parse a user command and dispatch. Emits an OTel span per command."""
        with self._tracer.start_as_current_span(f"dispatcher.command.{command}") as span:
            entry = self._agent_handlers.get(command)
            if entry is None:
                raise ValueError(f"Unknown command: /{command}")
            agent, handler = entry
            span.set_attribute(DISPATCHER_COMMAND, command)
            span.set_attribute(DISPATCHER_USER, user_id)
            span.set_attribute(DISPATCHER_AGENT, agent)

            task = Task(
                id=uuid.uuid4(),
                agent=agent,
                trigger="command",
                payload={"user_id": user_id, "command": command, "args": args or []},
                created_at=datetime.now(UTC),
            )
            span.set_attribute("dispatcher.task_id", str(task.id))

            logger.info("dispatching_command",
                task_id=str(task.id), command=command, agent=agent, args=args or [])
            try:
                result: TaskResult = await handler(task)
                span.set_attribute("dispatcher.success", result.success)
                if result.error:
                    span.set_attribute("dispatcher.error", result.error)
                return result
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                logger.exception("command_failed", task_id=str(task.id), command=command)
                return TaskResult(
                    task_id=task.id, success=False, output=None, error=str(exc), duration_ms=0,
                )

    async def handle_callback(self, callback_data: dict[str, Any]) -> TaskResult:
        """Handle an inline button callback. Emits an OTel span per callback."""
        command = callback_data.get("command", "")
        with self._tracer.start_as_current_span(f"dispatcher.callback.{command}") as span:
            entry = self._agent_handlers.get(command)
            if entry is None:
                raise ValueError(f"Unknown callback command: {command}")
            agent, handler = entry
            span.set_attribute(DISPATCHER_COMMAND, command)
            span.set_attribute(DISPATCHER_AGENT, agent)
            span.set_attribute("dispatcher.callback", True)

            task = Task(
                id=uuid.uuid4(),
                agent=agent,
                trigger="callback",
                payload=callback_data,
                created_at=datetime.now(UTC),
            )
            span.set_attribute("dispatcher.task_id", str(task.id))
            logger.info("dispatching_callback",
                task_id=str(task.id), command=command, external_id=callback_data.get("external_id"))
            try:
                result: TaskResult = await handler(task)
                span.set_attribute("dispatcher.success", result.success)
                return result
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                logger.exception("callback_failed", task_id=str(task.id), command=command)
                return TaskResult(
                    task_id=task.id, success=False, output=None, error=str(exc), duration_ms=0,
                )

    async def trigger_scheduled(self, job_id: str) -> TaskResult | None:
        """Execute a scheduled job."""
        logger.info("triggering_scheduled", job_id=job_id)
        return None
