"""Tests for Telegram command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backbone.dispatcher.dispatcher import Dispatcher
from backbone.dispatcher.task import TaskResult


@pytest.fixture
def mock_update() -> MagicMock:
    """Fixture: a mock Telegram Update."""
    update = MagicMock()
    update.effective_user.id = 12345
    update.effective_message.reply_text = AsyncMock()
    update.effective_message.reply_markdown_v2 = AsyncMock()
    return update


@pytest.fixture
def mock_context() -> MagicMock:
    """Fixture: a mock Context with a dispatcher."""
    context = MagicMock()
    context.args = ["now"]
    context.bot_data = {"dispatcher": MagicMock(spec=Dispatcher)}
    return context


@pytest.mark.asyncio
async def test_dispatcher_not_initialised(mock_update: MagicMock, mock_context: MagicMock) -> None:
    """When dispatcher is None, command returns an error message."""
    from backbone.telegram.handlers.commands import command_digest

    mock_context.bot_data["dispatcher"] = None
    await command_digest(mock_update, mock_context)

    mock_update.effective_message.reply_text.assert_awaited_once()
    call_args = mock_update.effective_message.reply_text.call_args
    assert "Dispatcher not initialised" in call_args[0][0]


@pytest.mark.asyncio
async def test_digest_now_dispatches(mock_update: MagicMock, mock_context: MagicMock) -> None:
    """Running /digest now dispatches to the agent."""
    from backbone.telegram.handlers.commands import command_digest

    mock_dispatcher = mock_context.bot_data["dispatcher"]
    mock_dispatcher.handle_command = AsyncMock(
        return_value=TaskResult(task_id=MagicMock(), success=True, output="Digest sent")
    )

    await command_digest(mock_update, mock_context)

    mock_dispatcher.handle_command.assert_awaited_once_with("12345", "digest", ["now"])
