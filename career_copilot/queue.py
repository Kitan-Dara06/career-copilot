"""Celery application + tasks for async LLM calls.

Only ``professor_brief`` uses the queue (Modal GPU via Celery).
All other LLM calls go through the synchronous ModelClient.

Broker: RabbitMQ (production) or Redis/Upstash (dev).
Redis (Upstash) used as result backend in both cases.
"""

from __future__ import annotations

import json
import os
from urllib.parse import quote_plus

from celery import Celery
from dotenv import load_dotenv

load_dotenv()  # Load .env before reading broker URL

from career_copilot.config import get_settings

settings = get_settings()


def _build_upstash_url() -> str:
    """Construct a TLS Redis URL from individual Upstash env vars.

    Uses ``rediss://`` (double-s for TLS).
    """
    host = os.getenv("UPSTASH_HOST", "")
    password = os.getenv("UPSTASH_PASSWORD", "")
    port = os.getenv("UPSTASH_PORT", "6379")

    if not host or not password:
        # Fall back to the single URL env var (local dev)
        return settings.upstash_redis_url

    encoded_password = quote_plus(password)
    return f"rediss://default:{encoded_password}@{host}:{port}"


def _get_broker() -> str:
    """Return the Celery broker URL.

    Prefers RabbitMQ if ``RABBITMQ_URL`` is set (production),
    otherwise falls back to Upstash Redis (dev).
    """
    rabbitmq = os.getenv("RABBITMQ_URL", "")
    if rabbitmq:
        return rabbitmq
    return _build_upstash_url()


app = Celery(
    "career_copilot",
    broker=_get_broker(),
    backend=None,  # Fire-and-forget: no result backend needed
    broker_connection_retry_on_startup=True,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
    task_acks_late=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "professor_brief.generate": {"queue": "brief"},
        "professor_brief.send_to_telegram": {"queue": "brief"},
    },
)


# JSON schema for the strict brief prompt (Gemini response_schema).
# Mirrors professor_brief_v2.yaml. Keep in sync with the prompt rules.
BRIEF_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "research_direction": {"type": "string"},
        "papers_to_read": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "venue_year": {"type": "string"},
                    "why_for_user": {"type": "string"},
                },
                "required": ["title", "venue_year", "why_for_user"],
            },
        },
        "fit": {
            "type": "object",
            "properties": {
                "strong": {"type": "array", "items": {"type": "string"}},
                "adjacent": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["strong", "adjacent"],
        },
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["research_direction", "papers_to_read", "fit", "next_steps"],
}


@app.task(name="professor_brief.generate")  # type: ignore[untyped-decorator]
def generate_professor_brief(
    prof_name: str,
    affiliation: str,
    recent_papers: str,
    user_interests: str,
    chat_id: str,
    homepage: str = "",
    overlap_score: float = 0.0,
) -> dict[str, object]:
    """Generate the LLM-powered sections of a professor brief.

    Routing order:
      1. Modal/Qwen (iff ``brief_via_modal`` is True in settings)
      2. Gemini (strict JSON mode via ModelClient.response_format)
      3. Raw-text fallback if JSON parsing fails

    The structured data (name, affiliation, papers, interests) is collected by
    the agent BEFORE enqueuing; this task only handles LLM generation.
    """
    from backbone.model_client import ModelClient
    from backbone.prompt_registry.loader import load as load_prompt, render

    template = load_prompt("paper_tracker", "professor_brief")
    prompt, _ = render(
        template,
        {
            "prof_name": prof_name,
            "affiliation": affiliation,
            "homepage": homepage,
            "recent_papers": recent_papers,
            "user_interests": user_interests,
        },
    )

    llm_output = ""
    used = "unknown"
    if settings.brief_via_modal:
        llm_output, used = _call_modal_qwen(prompt)

    if not llm_output:
        client = ModelClient()
        try:
            llm_output = client.generate_sync(
                model=template.model.name or "gemini-2.5-flash",
                prompt=prompt,
                temperature=template.model.temperature,
                max_tokens=template.model.max_tokens,
                response_format="json",
                response_schema=BRIEF_RESPONSE_SCHEMA,
            )
            used = "gemini-json"
        except Exception as exc:
            send_brief_to_telegram.delay(
                chat_id=chat_id,
                text=f"Could not generate the brief for {prof_name} ({exc})",
            )
            return {"success": False, "error": str(exc)[:200]}

    parsed = _try_parse_brief_json(llm_output)
    if parsed is not None:
        brief = _format_brief_json(
            parsed,
            prof_name=prof_name,
            affiliation=affiliation,
            homepage=homepage,
            overlap_score=overlap_score,
        )
    else:
        # Free-text fallback: strip noise the LLM may still add.
        brief = _format_brief_text(
            llm_output,
            prof_name=prof_name,
            affiliation=affiliation,
            homepage=homepage,
            overlap_score=overlap_score,
        )

    send_brief_to_telegram.delay(chat_id=chat_id, text=brief)
    return {"success": True, "text": llm_output[:200], "used": used}


def _call_modal_qwen(prompt: str) -> tuple[str, str]:
    """Invoke the Modal Qwen worker via subprocess. Returns (text, model label).

    Returns ("", "modal-skipped") on any failure so the caller falls back.
    """
    import subprocess
    import sys

    payload = json.dumps({"prompt_name": "professor_brief", "inputs": {}, "raw_prompt": prompt})
    try:
        result = subprocess.run(
            [sys.executable, "-m", "deploy.modal.brief_worker", "--payload", payload],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:
        return "", "modal-error"
    if result.returncode != 0:
        return "", "modal-error"
    return result.stdout.strip(), "modal-qwen"


def _try_parse_brief_json(text: str) -> dict[str, object] | None:
    """Best-effort JSON extraction from the model output (shares parser logic).

    Wraps :func:`backbone.model_client.parse_loose_json` and adds a brief-shape
    sanity check so free-text model output doesn't sneak through as JSON.
    """
    from backbone.model_client import parse_loose_json

    parsed = parse_loose_json(text)
    if not isinstance(parsed, dict):
        return None
    # Minimal shape check: a brief must mention these top-level fields.
    if "research_direction" not in parsed and "papers_to_read" not in parsed:
        return None
    return parsed


def _clean_inline(value: str | None) -> str:
    """Strip emoji, markdown emphasis, and meta-intro from a brief text block."""
    import re

    if not value:
        return ""
    text = str(value)
    text = re.sub(
        r"\A\s*(Here is a professor brief|Okay, I can|Based on the provided|"
        r"Sure,|I'll help|Let me build|Professor:).*?\n\n?",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)  # strip ### headers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # unbold **bold**
    text = re.sub(r"__([^_]+)__", r"\1", text)  # unbold __bold__
    text = re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
        r"\U00002702-\U000027B0\U0000FE0F\u200d\u2705\u26A0\u274C"
        r"\u2714\u2716\u2611\U0001F4CC\U0001F4CB\U0001F517"
        r"\U0001F4CA\U0001F4A1\U0001F4DD]+",
        "",
        text,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_brief_header(
    prof_name: str, affiliation: str, homepage: str, overlap_score: float
) -> str:
    """Build the metadata header that tops every brief message."""
    lines = [f"Professor Brief — {prof_name}"]
    if affiliation:
        lines.append(affiliation)
    if homepage:
        lines.append(homepage)
    if overlap_score and overlap_score > 0:
        lines.append(f"Similarity to your interests: {int(round(overlap_score * 100))}%")
    header = "\n".join(lines)
    header += "\n" + ("─" * 40) + "\n"
    return header


def _format_brief_json(
    parsed: dict[str, object],
    *,
    prof_name: str,
    affiliation: str,
    homepage: str,
    overlap_score: float,
) -> str:
    """Render a parsed JSON brief as clean plain text for Telegram."""
    out = _format_brief_header(prof_name, affiliation, homepage, overlap_score)

    direction = _clean_inline(parsed.get("research_direction"))  # type: ignore[arg-type]
    if direction:
        out += f"\nRECENT DIRECTION\n{direction}\n"

    papers = parsed.get("papers_to_read") or []
    if isinstance(papers, list) and papers:
        out += "\n3 PAPERS TO READ IF APPLYING\n"
        for i, p in enumerate(papers[:3], 1):
            if not isinstance(p, dict):
                continue
            title = _clean_inline(p.get("title"))  # type: ignore[arg-type]
            venue = _clean_inline(p.get("venue_year"))  # type: ignore[arg-type]
            why = _clean_inline(p.get("why_for_user"))  # type: ignore[arg-type]
            if not title:
                continue
            out += f"\n{i}. {title}"
            if venue:
                out += f" ({venue})"
            if why:
                out += f"\n   {why}"
        out += "\n"

    fit = parsed.get("fit")
    if isinstance(fit, dict) and (fit.get("strong") or fit.get("adjacent")):
        out += "\nCONNECTION TO YOUR INTERESTS\n"
        strong = fit.get("strong") or []
        adjacent = fit.get("adjacent") or []
        if isinstance(strong, list) and strong:
            out += "Strong:\n"
            for line in strong:
                if line:
                    out += f"  • {_clean_inline(line)}\n"
        if isinstance(adjacent, list) and adjacent:
            out += "Adjacent:\n"
            for line in adjacent:
                if line:
                    out += f"  • {_clean_inline(line)}\n"

    steps = parsed.get("next_steps") or []
    if isinstance(steps, list) and steps:
        out += "\nNEXT STEPS\n"
        for i, step in enumerate(steps, 1):
            if step:
                out += f"{i}. {_clean_inline(step)}\n"

    return out.strip() + "\n"


def _format_brief_text(
    text: str,
    *,
    prof_name: str,
    affiliation: str,
    homepage: str,
    overlap_score: float,
) -> str:
    """Fallback formatter when the model returned free text instead of JSON."""
    cleaned = _clean_inline(text)
    return _format_brief_header(prof_name, affiliation, homepage, overlap_score) + cleaned.strip() + "\n"


@app.task(name="professor_brief.send_to_telegram")  # type: ignore[untyped-decorator]
def send_brief_to_telegram(chat_id: str, text: str) -> dict[str, object]:
    """Send the generated brief back to Telegram, splitting if needed."""
    import httpx

    from career_copilot.config import get_settings

    settings = get_settings()
    token = settings.telegram_bot_token
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Telegram message limit is 4096; stay under with safe margin
    max_len = 4000
    chunks = _split_text(text, max_len)
    last_id = None

    for i, chunk in enumerate(chunks):
        payload: dict[str, object] = {"chat_id": chat_id, "text": chunk}
        try:
            resp = httpx.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            last_id = resp.json().get("result", {}).get("message_id")
        except Exception as exc:
            if i == 0:
                return {"success": False, "error": str(exc)}
            # Partial failure on subsequent chunks — non-fatal
            break

    return {"success": True, "message_id": last_id, "chunks": len(chunks)}


def _split_text(text: str, max_len: int) -> list[str]:
    """Split text into chunks under max_len, breaking at paragraph boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n")
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 > max_len and current:
            chunks.append(current)
            current = para
        else:
            current = (current + "\n" + para) if current else para
    if current:
        chunks.append(current)
    return chunks
