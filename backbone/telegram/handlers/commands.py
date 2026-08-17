"""Telegram command handlers — parse commands, call dispatcher, format responses."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from backbone.dispatcher.task import TaskResult

logger = logging.getLogger("telegram.commands")


async def _dispatch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
    args: list[str] | None = None,
) -> None:
    """Dispatch a command via the dispatcher and reply with the result."""
    msg = update.effective_message
    if msg is None:
        return

    from career_copilot.config import get_settings
    settings = get_settings()
    allowed_ids = settings.telegram_chat_id
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if allowed_ids and chat_id and allowed_ids != chat_id:
        await msg.reply_text("Access denied.")
        return

    dispatcher = context.bot_data.get("dispatcher")
    if dispatcher is None:
        await msg.reply_text("⚠️ Dispatcher not initialised.")
        return

    user_id = str(update.effective_user.id) if update.effective_user else "unknown"

    try:
        result: TaskResult = await dispatcher.handle_command(user_id, command, args)
        if result.success:
            text = str(result.output) if result.output else "✅ Done."
        else:
            text = f"❌ Error: {result.error}"
    except ValueError as exc:
        text = f"⚠️ {exc}"

    await msg.reply_text(text)


async def command_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/digest`` — subcommands: now, on, off, at."""
    args = context.args or []
    await _dispatch(update, context, "digest", args)


async def command_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/watch`` — subcommands: add, list, remove."""
    args = context.args or []
    await _dispatch(update, context, "watch", args)


async def command_discover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/discover`` — find professors matching your interests."""
    await _dispatch(update, context, "discover")


async def command_prof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/prof <name>`` — get a professor brief."""
    args = context.args or []
    await _dispatch(update, context, "prof", args)


async def command_interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/interests`` — show current interest vector summary."""
    await _dispatch(update, context, "interests")


async def command_export_zotero(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/export zotero <arxiv_id>``."""
    args = context.args or []
    await _dispatch(update, context, "export", args)


async def command_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/job <URL or text>`` — single posting lookup."""
    args = context.args or []
    await _dispatch(update, context, "job", args)


async def command_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/research <company>`` — pre-research flow."""
    args = context.args or []
    await _dispatch(update, context, "research", args)


async def command_contrib(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /contrib [topic] — find contribution opportunities."""
    args = context.args or []
    await _dispatch(update, context, "contrib", args)


async def command_opportunity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /opportunity <id> — get details."""
    args = context.args or []
    await _dispatch(update, context, "opportunity", args)


async def command_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/ask <message>`` — route a free-form request to Hermes."""
    msg = update.effective_message
    if msg is None:
        return

    from career_copilot.config import get_settings
    settings = get_settings()
    allowed_ids = settings.telegram_chat_id
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if allowed_ids and chat_id and allowed_ids != chat_id:
        await msg.reply_text("Access denied.")
        return

    args = context.args or []
    if not args:
        await msg.reply_text("Usage: /ask <your request>")
        return

    text = " ".join(args)
    await msg.reply_text("Thinking…")

    from career_copilot.hermes_bridge import HermesBridge, HermesBridgeError
    bridge = HermesBridge()
    try:
        response = await bridge.submit(text)
    except HermesBridgeError as exc:
        await msg.reply_text(f"⚠️ Hermes error: {exc}")
        return

    await msg.reply_text(response)


async def command_help(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/help`` — show available commands."""
    help_text = (
        "Career Copilot\n"
        "\n"
        "Digest\n"
        "  /digest now        Run the digest now\n"
        "  /digest on         Enable daily digest\n"
        "  /digest off        Disable daily digest\n"
        "  /digest at HH:MM   Set digest time\n"
        "\n"
        "Watchlist\n"
        "  /watch add <name>  Add a professor\n"
        "  /watch list        Show your watchlist\n"
        "  /watch remove <n>  Remove a professor\n"
        "\n"
        "Discovery\n"
        "  /discover          Find professors matching your interests\n"
        "  /prof <name>       Get a professor brief\n"
        "  /interests          Show your research interests\n"
        "  /export zotero <id> Export a paper to Zotero\n"
        "Job Hunter\n"
        "  /jobs [region]     Run job discovery\n"
        "  /job <URL/text>    Look up a posting\n"
        "  /research <name>   Research a company\n"
        "  /companies         List/add/remove companies\n"
        "  /prefs             Show/update career prefs\n"
        "  /saved             View saved postings\n"
        "  /help_jh           Job Hunter help\n"
        "\n"
        "Contribution Finder\n"
        "  /contrib [topic]   Find OSS opportunities\n"
        "  /opportunity <id>  Get opportunity details\n"
        "\n"
        "Ask (Hermes)\n"
        "  /ask <request>     Ask anything in natural language\n"
        "\n"
        "  /help              Show this message"
    )
    msg = update.effective_message
    if msg:
        await msg.reply_text(help_text)

async def command_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/jobs [region]`` — run job discovery."""
    args = context.args or []
    await _dispatch(update, context, "jobs", args)


async def command_companies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/companies [region]`` — list watchlist companies."""
    args = context.args or []
    await _dispatch(update, context, "companies", args)


async def command_saved(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/saved`` — view saved postings."""
    await _dispatch(update, context, "saved")


async def command_prefs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/prefs`` — show career preferences."""
    await _dispatch(update, context, "prefs")


async def command_jh_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/help_jh`` — show Job Hunter help."""
    await _dispatch(update, context, "help_jh")

