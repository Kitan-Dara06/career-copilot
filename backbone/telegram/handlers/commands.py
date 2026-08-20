"""Telegram command handlers — parse commands, call dispatcher, format responses."""

from __future__ import annotations

import logging
from typing import Any

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


# ── Hermes conversational layer ─────────────────────────────

# Active Hermes runs per chat: chat_id -> asyncio.Task. Used for
# concurrency guards and /cancel.
_hermes_runs: dict[str, Any] = {}


def _chat_id(update: Update) -> str:
    """Return the effective chat id as a string, or 'default'."""
    if update.effective_chat:
        return str(update.effective_chat.id)
    return "default"


def _is_allowed(update: Update) -> bool:
    """Chat allowlist check — shared by all Hermes entry points."""
    from career_copilot.config import get_settings

    allowed_ids = get_settings().telegram_chat_id
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    return not (allowed_ids and chat_id and allowed_ids != chat_id)


async def _hermes_respond(msg: Any, chat_id: str, text: str) -> None:
    """Run a Hermes request and reply when done.

    Runs in a background task so a slow agent loop does not block the
    Telegram polling loop. Cancellation propagates cleanly.
    """
    import asyncio

    from career_copilot.hermes_bridge import HermesBridgeError, get_bridge

    bridge = get_bridge()
    try:
        response = await bridge.submit(text, chat_id=chat_id)
    except asyncio.CancelledError:
        raise
    except HermesBridgeError as exc:
        response = f"⚠️ Hermes error: {exc}"
    finally:
        _hermes_runs.pop(chat_id, None)
    try:
        await msg.reply_text(response)
    except Exception:
        pass  # Telegram delivery failure is not worth crashing the loop


async def _ask_hermes(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Shared entry point for /ask and free-form messages."""
    import asyncio

    msg = update.effective_message
    if msg is None or not text.strip():
        return
    if not _is_allowed(update):
        await msg.reply_text("Access denied.")
        return

    chat_id = _chat_id(update)
    existing = _hermes_runs.get(chat_id)
    if existing is not None and not existing.done():
        await msg.reply_text("Still working on your previous request…")
        return

    await msg.reply_text("Thinking…")
    task = asyncio.create_task(_hermes_respond(msg, chat_id, text.strip()))
    _hermes_runs[chat_id] = task


async def command_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/ask <message>`` — route a free-form request to Hermes."""
    msg = update.effective_message
    if msg is None:
        return
    args = context.args or []
    if not args:
        await msg.reply_text("Usage: /ask <your request>")
        return
    await _ask_hermes(update, context, " ".join(args))


async def command_freeform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route plain-text messages (no leading slash) to Hermes."""
    msg = update.effective_message
    if msg is None or not msg.text:
        return
    text = msg.text.strip()
    if not text or text.startswith("/"):
        return  # commands are handled by CommandHandler
    await _ask_hermes(update, context, text)


async def command_cancel(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/cancel`` — cancel the active Hermes run for this chat."""
    msg = update.effective_message
    if msg is None:
        return
    if not _is_allowed(update):
        await msg.reply_text("Access denied.")
        return
    task = _hermes_runs.get(_chat_id(update))
    if task is not None and not task.done():
        task.cancel()
        await msg.reply_text("Cancelled.")
    else:
        await msg.reply_text("Nothing in progress.")


async def command_new(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/new`` — reset the Hermes conversation for this chat."""
    msg = update.effective_message
    if msg is None:
        return
    if not _is_allowed(update):
        await msg.reply_text("Access denied.")
        return
    from career_copilot.hermes_bridge import get_bridge

    get_bridge().clear_history(_chat_id(update))
    await msg.reply_text("Conversation reset.")


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
        "Planning\n"
        "  /workspace        List workspaces (use <id> to switch)\n"
        "  /proposals        Pending writes: Approve/Skip buttons\n"
        "  /approve <id>     Approve a pending write\n"
        "  /skip <id>        Skip a pending write\n"
        "\n"
        "Ask (Hermes)\n"
        "  <message>          Ask anything in natural language\n"
        "  /ask <request>     Same, explicit\n"
        "  /cancel            Cancel the active request\n"
        "  /new               Reset the conversation\n"
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


# ── Planner (Phase 2 planning workspace) ──────────────────────


async def command_workspace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/workspace [list|use <id>]`` — planning workspaces."""
    args = context.args or []
    await _dispatch(update, context, "workspace", args)


async def command_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/approve <proposal_id>`` — apply a pending planning write."""
    args = context.args or []
    await _dispatch(update, context, "approve", args)


async def command_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/skip <proposal_id>`` — dismiss a pending planning write."""
    args = context.args or []
    await _dispatch(update, context, "skip", args)


async def command_proposals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ``/proposals`` — list pending writes with Approve/Skip buttons."""
    import json

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from backbone.mcp import planning_writes as pw

    msg = update.effective_message
    if msg is None:
        return
    if not _is_allowed(update):
        await msg.reply_text("Access denied.")
        return

    proposals = await pw.list_pending_proposals(pw.DEFAULT_CHAT)
    if not proposals:
        await msg.reply_text("No pending proposals.")
        return

    lines = [f"{len(proposals)} pending proposal(s)", ""]
    buttons: list[list[Any]] = []
    for p in proposals:
        pid = p["proposal_id"]
        lines.append(f"{pid}. [{p['risk_level']}] {p['summary']}")
        buttons.append(
            [
                InlineKeyboardButton(
                    f"✅ Approve {pid}",
                    callback_data=json.dumps({"command": "proposal_approve", "item_id": pid}),
                ),
                InlineKeyboardButton(
                    f"⏭️ Skip {pid}",
                    callback_data=json.dumps({"command": "proposal_skip", "item_id": pid}),
                ),
            ]
        )
    await msg.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

