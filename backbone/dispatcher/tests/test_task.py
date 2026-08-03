"""Tests for Task and TaskResult types."""

from __future__ import annotations

import uuid

from backbone.dispatcher.task import Task, TaskResult


def test_task_creation() -> None:
    """Task is created with the expected attributes."""
    task = Task(
        id=uuid.uuid4(),
        agent="paper_tracker",
        trigger="command",
        payload={"command": "digest", "args": ["now"]},
    )
    assert task.agent == "paper_tracker"
    assert task.trigger == "command"
    assert task.payload["command"] == "digest"


def test_task_result_creation() -> None:
    """TaskResult is created with the expected attributes."""
    task_id = uuid.uuid4()
    result = TaskResult(
        task_id=task_id,
        success=True,
        output="Digest sent",
        duration_ms=1500,
    )
    assert result.success is True
    assert result.output == "Digest sent"
    assert result.error is None
    assert result.duration_ms == 1500


def test_task_result_failure() -> None:
    """Failed TaskResult carries the error."""
    task_id = uuid.uuid4()
    result = TaskResult(
        task_id=task_id,
        success=False,
        error="arXiv API timeout",
    )
    assert result.success is False
    assert result.error == "arXiv API timeout"
