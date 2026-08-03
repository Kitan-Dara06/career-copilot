"""Task and TaskResult types — unit of work flowing through the dispatcher."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal


class Task:
    """A unit of work flowing through the dispatcher to an agent.

    Attributes:
        id: Unique task identifier.
        agent: The agent that should handle this task (e.g. ``"paper_tracker"``).
        trigger: How the task was initiated.
        payload: Arbitrary data the agent receives.
        created_at: When the task was created.
    """

    def __init__(
        self,
        id: uuid.UUID,
        agent: str,
        trigger: Literal["command", "schedule", "callback", "event"],
        payload: dict[str, Any],
        created_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.agent = agent
        self.trigger = trigger
        self.payload = payload
        self.created_at = created_at or datetime.now(UTC)


class TaskResult:
    """Result of executing a Task.

    Attributes:
        task_id: The original task's ID.
        success: Whether the task completed without error.
        output: Agent response (text, dict, etc.).
        error: Error message if the task failed.
        duration_ms: Execution time in milliseconds.
    """

    def __init__(
        self,
        task_id: uuid.UUID,
        success: bool,
        output: Any = None,
        error: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        self.task_id = task_id
        self.success = success
        self.output = output
        self.error = error
        self.duration_ms = duration_ms
