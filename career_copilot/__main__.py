"""Main entry point for Career Copilot.

Usage:
    python -m career_copilot                  # prints OK
    python -m career_copilot serve            # webhook (needs public URL)
    python -m career_copilot serve --polling  # local dev (no webhook needed)
    python -m career_copilot worker           # scheduled job worker
"""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("serve", "worker"):
        print("OK")
        return

    import asyncio

    if sys.argv[1] == "worker":
        from career_copilot.worker import run_worker

        asyncio.run(run_worker())
        return

    mode = "webhook" if "--polling" not in sys.argv else "polling"

    if mode == "polling":
        from career_copilot.app import run_polling

        asyncio.run(run_polling())
    else:
        import uvicorn

        from career_copilot.config import configure_logging

        configure_logging(json_output=False)
        print("🚀 Career Copilot — webhook mode")
        print("   Listening on http://0.0.0.0:8080")
        print("   /health  /webhook")
        uvicorn.run(
            "career_copilot.app:create_app",
            host="0.0.0.0",
            port=8080,
            factory=True,
        )


if __name__ == "__main__":
    main()
