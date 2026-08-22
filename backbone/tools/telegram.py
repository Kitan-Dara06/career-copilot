"""Telegram send tools — message, digest, and card.

Sends via the Telegram Bot API using httpx.
Uses the bot token from settings.ctx.settings.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext


class SendMessageInput(BaseModel):
    chat_id: str
    text: str
    reply_markup: dict[str, Any] | None = None


class SendMessageOutput(BaseModel):
    message_id: str


class DigestItem(BaseModel):
    title: str
    authors: str
    why: str
    arxiv_id: str
    stream: str
    professor: str = ""  # Professor name for brief button


class SendDigestInput(BaseModel):
    chat_id: str
    items: list[DigestItem]


class SendDigestOutput(BaseModel):
    message_id: str


class Card(BaseModel):
    title: str
    body: str
    buttons: list[dict[str, Any]]


class SendCardInput(BaseModel):
    chat_id: str
    card: Card


class SendCardOutput(BaseModel):
    message_id: str


def _clean(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    for ch in "_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _button_token(name: str) -> str:
    """Short alphanumeric token for a professor in callback_data.

    Telegram caps callback_data at 64 bytes, and a full professor name
    (or the JSON wrapper) blows past it, so the whole message is rejected.
    We pass the last name instead and resolve it server-side (handle_brief).
    """
    import re

    last = (name or "").strip().split()[-1] if (name or "").strip() else ""
    token = re.sub(r"[^A-Za-z0-9]", "", last)
    return (token or "unknown")[:10]


async def _post(settings: Any, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = settings.telegram_bot_token
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


class SendMessageTool(Tool[SendMessageInput, SendMessageOutput]):
    name = "telegram.send_message"
    description = "Send a plain text message to a Telegram chat."
    input_schema = SendMessageInput
    output_schema = SendMessageOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: SendMessageInput) -> SendMessageOutput:
        data = await _post(
            ctx.settings, "sendMessage", {"chat_id": input.chat_id, "text": input.text}
        )
        msg_id = str(data.get("result", {}).get("message_id", "unknown"))
        return SendMessageOutput(message_id=msg_id)


class SendDigestTool(Tool[SendDigestInput, SendDigestOutput]):
    name = "telegram.send_digest"
    description = "Send a paper digest with papers and action links."
    input_schema = SendDigestInput
    output_schema = SendDigestOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: SendDigestInput) -> SendDigestOutput:
        if not input.items:
            return SendDigestOutput(message_id="empty")

        from datetime import datetime

        today = datetime.utcnow().strftime("%Y-%m-%d")
        interest = [i for i in input.items if i.stream == "interest"]
        professor = [i for i in input.items if i.stream == "professor"]

        # Header message
        await _send_msg(ctx, input.chat_id, f"📚 arXiv digest — {today}")

        last_id = ""

        # Interest section
        if interest:
            await _send_msg(ctx, input.chat_id, "— by interest —")
            for idx, item in enumerate(interest, 1):
                title = item.title[:150]
                authors = item.authors[:150]
                why = item.why[:200]
                url = f"https://arxiv.org/abs/{item.arxiv_id}"
                text = f"{idx}. {title}\n{url}\n{authors}\nWhy: {why}"
                keyboard = [
                    [
                        {
                            "text": "Read",
                            "callback_data": f'{{"command":"read","item_id":"{item.arxiv_id}"}}',
                        },
                        {
                            "text": "Save",
                            "callback_data": f'{{"command":"save","item_id":"{item.arxiv_id}"}}',
                        },
                        {
                            "text": "Skip",
                            "callback_data": f'{{"command":"skip","item_id":"{item.arxiv_id}"}}',
                        },
                    ]
                ]
                data = await _post(
                    ctx.settings,
                    "sendMessage",
                    {
                        "chat_id": input.chat_id,
                        "text": text,
                        "reply_markup": {"inline_keyboard": keyboard},
                        "disable_web_page_preview": True,
                    },
                )
                last_id = str(data.get("result", {}).get("message_id", ""))

        # Professor section
        if professor:
            await _send_msg(ctx, input.chat_id, "— by professor —")
            for idx, item in enumerate(professor, 1):
                title = item.title[:150]
                authors = item.authors[:150]
                why = item.why[:200]
                url = f"https://arxiv.org/abs/{item.arxiv_id}"
                text = f"🎓 {idx}. {title}\n{url}\n{authors}\nWhy: {why}"
                keyboard = [
                    [
                        {
                            "text": "Read",
                            "callback_data": f'{{"command":"read","item_id":"{item.arxiv_id}"}}',
                        },
                        {
                            "text": "Save",
                            "callback_data": f'{{"command":"save","item_id":"{item.arxiv_id}"}}',
                        },
                        {
                            "text": "Skip",
                            "callback_data": f'{{"command":"skip","item_id":"{item.arxiv_id}"}}',
                        },
                        {
                            "text": "📋 Brief",
                            "callback_data": (
                                f'{{"command":"brief","item_id":"{item.arxiv_id}",'
                                f'"p":"{_button_token(item.professor)}"}}'
                            ),
                        },
                    ]
                ]
                data = await _post(
                    ctx.settings,
                    "sendMessage",
                    {
                        "chat_id": input.chat_id,
                        "text": text,
                        "reply_markup": {"inline_keyboard": keyboard},
                        "disable_web_page_preview": True,
                    },
                )
                last_id = str(data.get("result", {}).get("message_id", ""))

        return SendDigestOutput(message_id=last_id)


async def _send_msg(ctx: ToolContext, chat_id: str, text: str) -> str:
    data = await _post(ctx.settings, "sendMessage", {"chat_id": chat_id, "text": text})
    return str(data.get("result", {}).get("message_id", ""))


class SendCardTool(Tool[SendCardInput, SendCardOutput]):
    name = "telegram.send_card"
    description = "Send a single rich card with action buttons."
    input_schema = SendCardInput
    output_schema = SendCardOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: SendCardInput) -> SendCardOutput:
        text = f"*{input.card.title}*\n\n{input.card.body}"
        data = await _post(
            ctx.settings,
            "sendMessage",
            {"chat_id": input.chat_id, "text": text, "parse_mode": "Markdown"},
        )
        msg_id = str(data.get("result", {}).get("message_id", "unknown"))
        return SendCardOutput(message_id=msg_id)


from backbone.tools.registry import register

register(SendMessageTool(), agent="paper_tracker")
register(SendDigestTool(), agent="paper_tracker")
register(SendCardTool(), agent="paper_tracker")
