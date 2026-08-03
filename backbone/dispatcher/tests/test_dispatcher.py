"""Tests for the Dispatcher."""

from __future__ import annotations

import pytest

from backbone.dispatcher.dispatcher import Dispatcher
from backbone.dispatcher.task import Task, TaskResult


async def _sample_handler(task: Task) -> TaskResult:
    """Test handler that echoes the command."""
    return TaskResult(
        task_id=task.id,
        success=True,
        output=f"Handled {task.payload.get('command', 'unknown')}",
        duration_ms=10,
    )


@pytest.mark.asyncio
async def test_register_and_dispatch() -> None:
    """Registering a command then dispatching it calls the handler."""
    d = Dispatcher()
    d.register_command("digest", "paper_tracker", _sample_handler)

    result = await d.handle_command("user-1", "digest", ["now"])
    assert result.success is True
    assert "Handled digest" in str(result.output)


@pytest.mark.asyncio
async def test_unknown_command_raises() -> None:
    """Dispatching an unregistered command raises ValueError."""
    d = Dispatcher()
    with pytest.raises(ValueError, match="Unknown command"):
        await d.handle_command("user-1", "nonexistent")


@pytest.mark.asyncio
async def test_callback_dispatch() -> None:
    """Dispatching a callback routes to the right handler."""
    d = Dispatcher()
    d.register_command("save", "paper_tracker", _sample_handler)

    result = await d.handle_callback({"command": "save", "item_id": "2607.14002"})
    assert result.success is True


@pytest.mark.asyncio
async def test_handler_exception_is_caught() -> None:
    """An exception in a handler returns a failed TaskResult (does not crash)."""

    async def failing_handler(_task: Task) -> TaskResult:
        raise RuntimeError("Something went wrong")

    d = Dispatcher()
    d.register_command("failing", "paper_tracker", failing_handler)

    result = await d.handle_command("user-1", "failing")
    assert result.success is False
    assert "Something went wrong" in (result.error or "")
