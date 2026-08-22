"""ModelClient — generic LLM interface for all synchronous LLM calls.

One client to rule them all:
    - Gemini (summarize_paper, professor_why, filter_decision, email_opener,
             professor_brief when not using Modal)
    - DeepSeek (why_relevant)

Qwen/Modal for professor_brief is handled by Celery (career_copilot.queue),
not by this client.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx
import structlog

from career_copilot.config import get_settings

logger = structlog.get_logger("model_client")


def parse_loose_json(raw: str) -> object | None:
    """Best-effort JSON extraction from an LLM response.

    Handles three common quirks even when ``response_format=json`` is set:
      1. Markdown code fences (\u0060\u0060\u0060json ... \u0060\u0060\u0060)
      2. Preamble such as ``"Here is the JSON requested:"`` before the object.
      3. Trailing prose after the closing brace.

    Returns the parsed object (dict or list) or None if no valid JSON object
    could be located in the response.
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip markdown code fences if present.
    fence = re.search(r"```(?:json|JSON)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find the first balanced JSON object in case prose remains around it.
    start = text.find("{")
    if start < 0:
        # Maybe it's a JSON array — handle that too.
        start = text.find("[")
        if start < 0:
            return None
        opener, closer = "[", "]"
    else:
        opener, closer = "{", "}"
    depth = 0
    end = -1
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
    if end < 0:
        return None
    snippet = text[start:end]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        return None


class ModelClient:
    """Generic LLM client. Every call emits an OTel span with gen_ai.* attrs."""

    def __init__(self) -> None:
        self._settings = get_settings()
        try:
            from backbone.observability import get_tracer
            self._tracer = get_tracer("model_client")
        except Exception:
            self._tracer = None

    async def generate(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 256,
        system_prompt: str = "",
        response_format: str | None = None,
        response_schema: dict | None = None,
        agent_name: str = "",
        prompt_name: str = "",
        prompt_version: int = 0,
    ) -> str:
        """Generate text. Wraps the call in an OTel span with gen_ai.* attrs."""
        from opentelemetry.trace import Status, StatusCode
        from backbone.observability import (
            LLM_SYSTEM, LLM_REQUEST_MODEL, LLM_REQUEST_MAX_TOKENS,
            LLM_REQUEST_TEMPERATURE, LLM_USAGE_INPUT, LLM_USAGE_OUTPUT,
            LLM_RESPONSE_FINISH, LLM_COST_USD, LLM_PROMPT_NAME,
            LLM_PROMPT_VERSION, LLM_AGENT,
        )
        if model.startswith("gemini"):
            system = "gemini"
        elif model.startswith("deepseek"):
            system = "deepseek"
        else:
            system = "unknown"
        with self._tracer.start_as_current_span(f"llm.{system}.{model}") as span:
            span.set_attribute(LLM_SYSTEM, system)
            span.set_attribute(LLM_REQUEST_MODEL, model)
            span.set_attribute(LLM_REQUEST_MAX_TOKENS, max_tokens)
            span.set_attribute(LLM_REQUEST_TEMPERATURE, temperature)
            if prompt_name:
                span.set_attribute(LLM_PROMPT_NAME, prompt_name)
                span.set_attribute(LLM_PROMPT_VERSION, prompt_version)
            if agent_name:
                span.set_attribute(LLM_AGENT, agent_name)
            if response_format:
                span.set_attribute("gen_ai.response.format", response_format)
            import time
            t0 = time.monotonic()
            try:
                if system == "gemini":
                    output = await self._call_gemini(
                        prompt, temperature, max_tokens, system_prompt, response_format, response_schema
                    )
                elif system == "deepseek":
                    output = await self._call_deepseek(
                        prompt, temperature, max_tokens,
                        response_format=response_format, response_schema=response_schema, model=model,
                    )
                else:
                    logger.warning("unknown_model", model=model)
                    output = ""
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            latency_ms = int((time.monotonic() - t0) * 1000)
            span.set_attribute("gen_ai.response.latency_ms", latency_ms)
            in_tok = len(prompt) // 4 + (len(system_prompt) // 4 if system_prompt else 0)
            out_tok = len(output) // 4
            span.set_attribute(LLM_USAGE_INPUT, in_tok)
            span.set_attribute(LLM_USAGE_OUTPUT, out_tok)
            pricing = {
                "gemini-2.5-flash": (0.10, 0.40),
                "deepseek-v4-flash": (0.07, 0.27),
                "deepseek-v4-pro": (0.60, 2.40),
            }
            in_price, out_price = pricing.get(model, (0.10, 0.40))
            cost = (in_tok / 1_000_000) * in_price + (out_tok / 1_000_000) * out_price
            span.set_attribute(LLM_COST_USD, cost)
            if output and len(output) < 40:
                span.set_attribute(LLM_RESPONSE_FINISH, "empty_or_short")
            # Log to prompt_runs table asynchronously (fire-and-forget)
            try:
                from backbone.prompt_registry.run_logger import PromptRunLogger, PromptRun
                logger_inst = PromptRunLogger()
                await logger_inst.log(PromptRun(
                    agent=agent_name or "unknown",
                    prompt_name=prompt_name or "unknown",
                    prompt_version=prompt_version,
                    model=model,
                    input_hash=hex(hash(prompt))[2:16],
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    latency_ms=latency_ms,
                    output=output[:1000] if output else "",
                    cost_usd=cost,
                ))
            except Exception:
                pass
            return output

    def generate_sync(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 256,
        system_prompt: str = "",
        response_format: str | None = None,
        response_schema: dict | None = None,
    ) -> str:
        """Generate text from the given model (sync, for Celery tasks)."""
        import asyncio

        return asyncio.run(
            self.generate(
                model,
                prompt,
                temperature,
                max_tokens,
                system_prompt,
                response_format,
                response_schema,
            )
        )

    async def _call_gemini(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        system: str = "",
        response_format: str | None = None,
        response_schema: dict | None = None,
    ) -> str:
        """Call Gemini API via the native generateContent endpoint."""
        api_key = self._settings.gemini_api_key
        if not api_key:
            return "REFUSED"

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={api_key}"
        )

        parts: list[dict[str, object]] = [{"text": prompt}]
        contents = [{"parts": parts, "role": "user"}]
        gen_config: dict[str, object] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if response_format == "json":
            gen_config["responseMimeType"] = "application/json"
            if response_schema is not None:
                # Gemini expects OpenAPI-style schema objects.
                gen_config["responseSchema"] = response_schema
        payload: dict[str, object] = {
            "contents": contents,
            "generationConfig": gen_config,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        last_exc = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(url, json=payload, timeout=30)
                    if resp.status_code == 429 and attempt < 2:
                        await asyncio.sleep((attempt + 1) * 3)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    return str(
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    ).strip()
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code if exc.response is not None else 0
                if code == 429 and attempt < 2:
                    await asyncio.sleep((attempt + 1) * 3)
                    continue
                last_exc = exc
                break
            except Exception as exc:
                last_exc = exc
                break
        logger.exception("gemini_call_failed", error=str(last_exc) if last_exc else "retries_exhausted")
        return ""  # all retries exhausted or network error

    async def _call_deepseek(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        response_format: str | None = None,
        response_schema: dict | None = None,
        model: str = "deepseek-v4-flash",
    ) -> str:
        """Call DeepSeek API (OpenAI-compatible endpoint).

        ``response_format="json"`` triggers OpenAI-compatible structured outputs:
        DeepSeek returns a single JSON object with no preamble. Much more
        reliable than Gemini 2.5-flash's responseSchema for short schema-shaped
        calls (professor_verify, etc.).
        """
        api_key = self._settings.deepseek_api_key
        if not api_key:
            logger.warning("deepseek_api_key_not_set")
            return ""

        messages = [{"role": "user", "content": prompt}]
        # Honor the model passed by the caller, mapping legacy account names to
        # the canonical v4 names DeepSeek currently accepts (the API rejects
        # "deepseek-pro" / "deepseek-chat" with a 400). JSON mode upgrades
        # flash to pro: v4-flash returns empty responses on large json_object
        # outputs (finish_reason=length) ~40% of the time.
        _DEEPSEEK_ALIASES = {
            "deepseek-chat": "deepseek-v4-pro",
            "deepseek-pro": "deepseek-v4-pro",
            "deepseek-reasoner": "deepseek-v4-pro",
            "deepseek-flash": "deepseek-v4-flash",
        }
        if model and model.startswith("deepseek-"):
            resolved_model = _DEEPSEEK_ALIASES.get(model, model)
        else:
            resolved_model = "deepseek-v4-flash"
        if response_format == "json" and resolved_model == "deepseek-v4-flash":
            resolved_model = "deepseek-v4-pro"
        payload: dict[str, object] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=60,  # v4-pro is slower, needs longer timeout
                )
                if resp.status_code >= 400:
                    # Capture the error body so we can diagnose 400s from
                    # DeepSeek (e.g. unsupported response_format / schema).
                    body = resp.text[:500]
                    logger.warning(
                        "deepseek_call_http_error",
                        status=resp.status_code,
                        body=body,
                    )
                    resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()  # type: ignore[no-any-return]
                finish_reason = data.get("choices", [{}])[0].get("finish_reason", "unknown")
                if not content:
                    # DeepSeek periodically returns empty content. Log the
                    # finish_reason + prompt-token estimate so we can see whether
                    # it's a length-cut issue, a content-policy refusal, or
                    # intermittent model flakiness.
                    logger.warning(
                        "deepseek_empty_response",
                        model=resolved_model,
                        finish_reason=finish_reason,
                        prompt_chars=len(prompt),
                        response_id=data.get("id", ""),
                    )
                elif len(content) < 40:
                    logger.warning(
                        "deepseek_short_response",
                        model=resolved_model,
                        content_preview=content,
                        finish_reason=finish_reason,
                    )
                return content
        except Exception:
            logger.exception("deepseek_call_failed", model=resolved_model)
            return "(unavailable)"  # Network error fallback
