"""Telegram bot initialisation and handler registration.

Builds a python-telegram-bot ``Application`` with command and callback
handlers registered from the ``handlers/`` subpackage.
"""

from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from backbone.dispatcher.dispatcher import Dispatcher
from career_copilot.config import Settings

from .handlers import callbacks, commands


def build_bot(
    settings: Settings, dispatcher: Dispatcher
) -> Application[Any, Any, Any, Any, Any, Any]:
    """Construct a python-telegram-bot Application with all handlers.

    Args:
        settings: App settings (must have ``telegram_bot_token``).
        dispatcher: The central dispatcher to route commands to.

    Returns:
        A configured ``Application`` ready to run.
    """
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .connect_timeout(15.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(5.0)
        .build()
    )

    # ── Command handlers ──
    application.add_handler(CommandHandler("digest", commands.command_digest))
    application.add_handler(CommandHandler("watch", commands.command_watch))
    application.add_handler(CommandHandler("discover", commands.command_discover))
    application.add_handler(CommandHandler("prof", commands.command_prof))
    application.add_handler(CommandHandler("interests", commands.command_interests))
    application.add_handler(CommandHandler("help", commands.command_help))
    application.add_handler(CommandHandler("jobs", commands.command_jobs))
    application.add_handler(CommandHandler("companies", commands.command_companies))
    application.add_handler(CommandHandler("saved", commands.command_saved))
    application.add_handler(CommandHandler("job", commands.command_job))
    application.add_handler(CommandHandler("research", commands.command_research))
    application.add_handler(CommandHandler("prefs", commands.command_prefs))
    application.add_handler(CommandHandler("contrib", commands.command_contrib))
    application.add_handler(CommandHandler("opportunity", commands.command_opportunity))
    application.add_handler(CommandHandler("help_jh", commands.command_jh_help))
    application.add_handler(CommandHandler("ask", commands.command_ask))
    application.add_handler(CommandHandler("cancel", commands.command_cancel))
    application.add_handler(CommandHandler("new", commands.command_new))
    application.add_handler(CommandHandler("workspace", commands.command_workspace))
    application.add_handler(CommandHandler("proposals", commands.command_proposals))
    application.add_handler(CommandHandler("proposal", commands.command_proposals))
    application.add_handler(CommandHandler("approve", commands.command_approve))
    application.add_handler(CommandHandler("skip", commands.command_skip))

    # ── Free-form text → Hermes ──
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, commands.command_freeform)
    )

    application.add_handler(CommandHandler("export", commands.command_export_zotero))
    application.add_handler(CommandHandler("start", commands.command_help))

    # ── Callback query handlers ──
    application.add_handler(CallbackQueryHandler(callbacks.callback_handler))

    # ── Stash dispatcher for handler access ──
    application.bot_data["dispatcher"] = dispatcher

    return application


def get_chat_id_from_update(update: Update) -> str | None:
    """Extract the chat ID from a Telegram Update.

    Returns:
        The chat ID as a string, or ``None`` if unavailable.
    """
    if update.effective_chat:
        return str(update.effective_chat.id)
    if update.callback_query and update.callback_query.message:
        return str(update.callback_query.message.chat.id)
    return None
