"""Hermes bridge — thin HTTP client to the Hermes Agent API server.

Career Copilot talks to Hermes over its OpenAI-compatible API server
(``hermes gateway`` on port 8642), not by importing Hermes directly. This
keeps Hermes's heavily-pinned dependency graph out of Career Copilot's
process and lets Hermes run in a separate container.

The bridge is intentionally narrow: it submits a message and returns the
final text. Conversation history is tracked per chat in-memory (bounded),
because the API server is stateless and every request must carry the full
conversation for multi-turn exchanges (e.g. Hermes clarification answers).
"""

from __future__ import annotations

from typing import Any

import httpx

from career_copilot.config import get_settings

# Keep the last 10 messages (5 turns) per chat — enough for clarification
# exchanges and follow-ups without unbounded memory growth.
MAX_HISTORY_MESSAGES = 10


class HermesBridgeError(Exception):
    """Raised when the Hermes API server cannot produce a response."""


class HermesBridge:
    """Submit messages to the Hermes Agent API server."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._history: dict[str, list[dict[str, str]]] = {}

    def clear_history(self, chat_id: str) -> None:
        """Forget the conversation for a chat (e.g. a /new command)."""
        self._history.pop(chat_id, None)

    async def submit(
        self,
        message: str,
        *,
        chat_id: str = "default",
        system_prompt: str | None = None,
        timeout: float = 120.0,
    ) -> str:
        """Send a message to Hermes and return the final text.

        The per-chat history is sent with every request (the API server is
        stateless) and updated with the exchange on success.

        Args:
            message: The user's latest message.
            chat_id: Conversation key; isolates history per chat.
            system_prompt: Optional system instruction for this turn.
            timeout: HTTP timeout in seconds. The agent loop can take a while.

        Returns:
            The final assistant text.

        Raises:
            HermesBridgeError: If the API is unreachable, misconfigured, or
                returns no content.
        """
        url = self._settings.hermes_api_url.rstrip("/") + "/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._settings.hermes_api_key:
            headers["Authorization"] = f"Bearer {self._settings.hermes_api_key}"

        history = self._history.get(chat_id, [])
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": message})

        payload: dict[str, Any] = {
            "model": self._settings.hermes_model,
            "messages": messages,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise HermesBridgeError(f"Hermes API unreachable: {exc}") from exc

        if resp.status_code != 200:
            raise HermesBridgeError(
                f"Hermes API returned {resp.status_code}: {resp.text[:300]}"
            )

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise HermesBridgeError(f"Hermes API response malformed: {resp.text[:300]}") from exc

        if not content:
            raise HermesBridgeError("Hermes API returned empty content")

        # Persist the exchange so a follow-up ("yes") has context.
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": content})
        self._history[chat_id] = history[-MAX_HISTORY_MESSAGES:]

        return content
