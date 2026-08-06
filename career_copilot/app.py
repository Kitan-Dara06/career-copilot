"""FastAPI application factory — used by the ``serve`` command.

Wires: FastAPI → Telegram webhook → Dispatcher → Agent handlers.
"""

from __future__ import annotations

import signal
from typing import Any

import structlog
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application

from backbone.dispatcher.dispatcher import Dispatcher
from backbone.dispatcher.wiring import wire_contribution_finder, wire_job_hunter, wire_paper_tracker
from backbone.observability import setup_telemetry
from backbone.telegram.bot import build_bot
from career_copilot.config import configure_logging, get_settings

logger = structlog.get_logger("app")


def create_app() -> FastAPI:
    """Create and return the fully-wired FastAPI application.

    Returns:
        A configured FastAPI instance with health + webhook endpoints.
    """
    setup_telemetry(service_name="career-copilot", service_version="0.2.0")
    configure_logging(json_output=False)
    settings = get_settings()

    # Init dispatcher + wire agent commands
    dispatcher = Dispatcher()
    wire_paper_tracker(dispatcher)
    wire_job_hunter(dispatcher)
    wire_contribution_finder(dispatcher)

    # Build Telegram bot Application
    bot_app: Application[Any, Any, Any, Any, Any, Any] = build_bot(settings, dispatcher)

    app = FastAPI(
        title="Career Copilot",
        version="0.1.0",
        description="Multi-agent personal assistant for academic career development.",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(request: Request) -> dict[str, str]:
        """Receive Telegram updates via webhook."""
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        if update is not None:
            await bot_app.process_update(update)
        return {"status": "ok"}

    # Stash bot app for lifecycle management
    app.state.bot_app = bot_app
    app.state.dispatcher = dispatcher

    return app


async def run_polling() -> None:
    """Start the Telegram bot in polling mode (no webhook needed).

    Use this for local dev. No ngrok or public URL required.
    """
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    setup_telemetry(service_name="career-copilot", service_version="0.2.0")
    setup_telemetry(service_name="career-copilot", service_version="0.2.0")
    configure_logging(json_output=False)
    settings = get_settings()

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set. Create a .env file first.")

    # Log the DB URL (masked) so we can debug connection issues
    _db_url = settings.database_url
    if '@' in _db_url:
        _proto, _rest = _db_url.split('://', 1)
        _creds, _host = _rest.split('@', 1)
        _db_url = f"{_proto}://***@{_host}"
    print(f"DB URL: {_db_url}")
    print(f"Qdrant URL: {settings.qdrant_url or '(not set)'}")
    logger.info("bot_starting", db_url=_db_url, qdrant_url=settings.qdrant_url)

    dispatcher = Dispatcher()
    wire_paper_tracker(dispatcher)
    wire_job_hunter(dispatcher)
    wire_contribution_finder(dispatcher)

    bot_app = build_bot(settings, dispatcher)

    logger.info("bot_starting_polling")
    print("Bot starting in polling mode...")
    print("   Paper Tracker: /digest now, /discover, /watch, /prof, /interests, /help")
    print("   Job Hunter:    /jobs, /companies, /saved, /prefs, /help_jh")
    print("   Contrib Finder: /contrib, /opportunity")
    print("   Press Ctrl+C to stop")

    await bot_app.initialize()
    await bot_app.start()
    assert bot_app.updater is not None
    await bot_app.updater.start_polling(
        poll_interval=2.0,
        timeout=30,
    )

    try:
        import asyncio, signal
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try: loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError: pass  # Windows
        logger.info('bot_running')
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        assert bot_app.updater is not None
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
