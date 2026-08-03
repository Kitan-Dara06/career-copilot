"""Telegram callback query handlers — inline button presses.

Each callback carries a JSON payload with ``command`` and optional args.
The dispatcher routes them to the appropriate agent.
"""

from __future__ import annotations

import json

import structlog
from telegram import Update
from telegram.ext import ContextTypes

logger = structlog.get_logger("telegram.callbacks")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Top-level callback handler: parse callback data and dispatch."""
    query = update.callback_query
    if query is None:
        return

    dispatcher = context.bot_data.get("dispatcher")
    if dispatcher is None:
        logger.warning("callback_no_dispatcher")
        await query.answer("⚠️ Dispatcher not initialised.")
        return

    # Parse callback data
    raw_data = query.data
    if raw_data is None:
        return

    logger.debug("callback_received", data=raw_data[:200])

    try:
        data = json.loads(raw_data)
    except (json.JSONDecodeError, TypeError):
        logger.warning("callback_invalid_json", data=raw_data[:200])
        await query.answer("⚠️ Invalid callback.")
        return

    command = data.get("command", "")
    if not command:
        return

    logger.info("callback_dispatching", command=command, item_id=data.get("item_id", ""))

    try:
        result = await dispatcher.handle_callback(data)
        if not result.success:
            logger.warning("callback_failed", command=command, error=result.error)
            await query.answer(f"❌ {result.error}", show_alert=True)
        else:
            # Show toast notification, keep the message intact
            await query.answer(str(result.output) if result.output else "✅ Done")
    except ValueError as exc:
        logger.warning("callback_unknown_command", command=command, error=str(exc))
        await query.answer(f"⚠️ {exc}")
