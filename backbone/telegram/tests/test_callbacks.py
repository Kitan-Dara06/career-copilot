"""Tests for Telegram callback handler."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backbone.dispatcher.dispatcher import Dispatcher
from backbone.dispatcher.task import TaskResult


@pytest.fixture
def mock_callback_update() -> MagicMock:
    """Fixture: a mock Update with a callback query."""
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


@pytest.fixture
def mock_context() -> MagicMock:
    """Fixture: mock context with dispatcher."""
    context = MagicMock()
    context.bot_data = {
        "dispatcher": MagicMock(spec=Dispatcher),
    }
    return context


@pytest.mark.asyncio
async def test_callback_read(mock_callback_update: MagicMock, mock_context: MagicMock) -> None:
    """Callback with command 'read' dispatches correctly."""
    from backbone.telegram.handlers.callbacks import callback_handler

    mock_callback_update.callback_query.data = json.dumps(
        {"command": "read", "item_id": "2607.14002"}
    )

    mock_dispatcher = mock_context.bot_data["dispatcher"]
    mock_dispatcher.handle_callback = AsyncMock(
        return_value=TaskResult(task_id=MagicMock(), success=True, output="✅ Marked as read")
    )

    await callback_handler(mock_callback_update, mock_context)

    mock_callback_update.callback_query.answer.assert_awaited_once()
    mock_dispatcher.handle_callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_invalid_json(
    mock_callback_update: MagicMock, mock_context: MagicMock
) -> None:
    """Invalid callback JSON shows an error."""
    from backbone.telegram.handlers.callbacks import callback_handler

    mock_callback_update.callback_query.data = "not-json"

    await callback_handler(mock_callback_update, mock_context)

    mock_callback_update.callback_query.answer.assert_awaited_once_with("⚠️ Invalid callback.")
