"""Hermes bridge — thin HTTP client to the Hermes Agent API server.

Career Copilot talks to Hermes over its OpenAI-compatible API server
(``hermes gateway`` on port 8642), not by importing Hermes directly. This
keeps Hermes's heavily-pinned dependency graph out of Career Copilot's
process and lets Hermes run in a separate container.

The bridge is intentionally narrow: it submits a message and returns the
final text. Conversation history is tracked per chat in-memory (bounded),
because the API server is stateless and every request must carry the full
conversation for multi-turn exchanges (e.g. Hermes clarification answers).

Use ``get_bridge()`` — a module singleton — so history survives across
requests. Constructing ``HermesBridge()`` directly starts with empty
history every time.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from career_copilot.config import get_settings

# Keep the last 10 messages (5 turns) per chat — enough for clarification
# exchanges and follow-ups without unbounded memory growth.
MAX_HISTORY_MESSAGES = 10

# Single-user platform: all planning state is keyed by this owner id.
PLANNING_USER = "aaliyah"


_SYSTEM_PROMPT: str | None = None

# The Hermes api_server IGNORES system-role messages, so guidance must be
# injected as a user-role block right before the user's message. This notice
# fixes two observed failure modes: (1) the model echoing its own earlier
# "tools not available" hallucination from chat history, and (2) flash-lite
# under-discovering the career.* tools when the request is free-form.
_ENV_NOTICE = (
    "[Environment] You have these tools, and they are all available and working: "
    "career.profile.get, career.papers.search, career.professors.search, "
    "career.professors.web_search, career.jobs.search, career.planning.get_summary, "
    "career.planning.list_workspaces, career.planning.get_workspace, "
    "career.planning.list_goals, career.planning.list_tasks, "
    "career.planning.list_decisions, career.planning.list_notes, "
    "career.planning.list_artifacts, and the career.planning write tools. "
    "Never claim a tool is missing or unavailable; if a tool call errors, read "
    "the error and retry or rephrase. For professor briefs or fit questions, call "
    "career.professors.search AND career.profile.get (for the user's interests), "
    "then answer from the returned data."
)

# Assistant messages that only repeat a tools-unavailable hallucination add
# noise and poison later turns; drop them from the history sent to Hermes.
_UNHEALTHY_REFUSAL_RE = re.compile(
    r"tool(s)? .{0,40}(not available|missing)|cannot fulfill this request",
    re.IGNORECASE,
)


def _default_system_prompt() -> str:
    """Build a small pointer prompt for Hermes.

    The user's canonical profile lives in the project YAML and is exposed to
    Hermes via the ``career.profile.get`` MCP tool. Rather than duplicating the
    data into the system prompt (bloat + drift when the YAML changes), we keep
    the prompt tiny and instruct Hermes to fetch the profile when relevant.
    """
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is not None:
        return _SYSTEM_PROMPT
    _SYSTEM_PROMPT = (
        "You are Career Copilot's conversational assistant for a CS student. "
        "The user's canonical research interests and skills are available via the "
        "career.profile.get tool. Before answering questions about professor fit, "
        "paper relevance, research direction, or career planning, call career.profile.get "
        "and ground your answer in its data. Use career.papers/professors/jobs.search "
        "when the user asks for papers, professors, or jobs. Keep replies concise. "
        "When listing papers or professors, use compact numbered lines and put a "
        "clickable URL on every entry; name the source (arxiv / csrankings / "
        "openalex / web) where relevant. "
        "For questions about the user's plan, goals, tasks, or decisions (e.g. "
        "'what did we decide on GRE?'), call the career.planning.get_summary / "
        "list_goals / list_tasks / list_decisions tools and ground the answer in "
        "the live data — never answer from assumption or memory. When you propose "
        "a planning write, call the career.planning.* write tool and then tell the "
        "user whether it was queued for approval or applied, with the exact "
        "/approve <id> or /skip <id> command to resolve it."
    )
    return _SYSTEM_PROMPT


class HermesBridgeError(Exception):
    """Raised when the Hermes API server cannot produce a response."""


async def _workspace_context(chat_id: str) -> str:
    """Compact planning snapshot prepended to a fresh session.

    Session continuity comes from the workspace, not Hermes memory (§4 of the
    harness design): on the first message of a chat we inject the current
    workspace snapshot so Hermes has state without chat history. The Hermes
    API server ignores ``system`` messages, so the snapshot is prepended as a
    user-role block instead.
    """
    try:
        from backbone.mcp.planning import get_active_workspace_id, get_summary

        wid = await get_active_workspace_id(chat_id)
        if wid is None:
            return ""
        summary = await get_summary(wid)
        if "error" in summary:
            return ""
        ws = summary.get("workspace", {}) or {}
        lines = [
            f"[Planning context — {ws.get('name', 'Workspace')} "
            f"({ws.get('intake_year', '')})]"
        ]
        goals = summary.get("open_goals_titles") or []
        if goals:
            lines.append("Open goals: " + "; ".join(goals[:5]))
        overdue = summary.get("overdue_tasks_titles") or []
        if overdue:
            lines.append("Overdue: " + "; ".join(overdue[:5]))
        decisions = summary.get("decisions") or []
        if decisions:
            lines.append(
                "Decisions: "
                + "; ".join(f"[{d['status']}] {d['title']}" for d in decisions[:6])
            )
        lines.append(f"{summary.get('total_tasks_open', 0)} open task(s).")
        return "\n".join(lines)
    except Exception:
        return ""


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
        user_id: str = PLANNING_USER,
        system_prompt: str | None = None,
        timeout: float = 120.0,
    ) -> str:
        """Send a message to Hermes and return the final text.

        The per-chat history is sent with every request (the API server is
        stateless) and updated with the exchange on success. Emits an OTel
        span ``hermes.submit`` with model, chat, and history size, and
        persists a ``hermes_runs`` row (§15) fire-and-forget.

        Args:
            message: The user's latest message.
            chat_id: Conversation key; isolates history per chat.
            user_id: Who is asking (used for run-level attribution).
            system_prompt: Optional system instruction for this turn.
            timeout: HTTP timeout in seconds. The agent loop can take a while.

        Returns:
            The final assistant text.

        Raises:
            HermesBridgeError: If the API is unreachable, misconfigured, or
                returns no content.
        """
        from datetime import UTC, datetime
        from uuid import uuid4

        from backbone.observability import (
            LLM_REQUEST_MODEL,
            LLM_RESPONSE_FINISH,
            LLM_USAGE_INPUT,
            LLM_USAGE_OUTPUT,
            get_tracer,
        )
        from backbone.hermes_observability import HermesRun, spawn_log_run

        run_id = uuid4().hex
        started_at = datetime.now(UTC)
        url = self._settings.hermes_api_url.rstrip("/") + "/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._settings.hermes_api_key:
            headers["Authorization"] = f"Bearer {self._settings.hermes_api_key}"

        history = self._history.get(chat_id, [])
        # Drop earlier assistant turns that only repeat a tools-unavailable
        # hallucination — they poison later turns and the api_server ignores
        # system messages, so a corrective notice is the only place to act.
        history = [h for h in history if not _UNHEALTHY_REFUSAL_RE.search(h.get("content", ""))]
        messages: list[dict[str, str]] = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        elif _default_system_prompt():
            messages.append({"role": "system", "content": _default_system_prompt()})
        messages.extend(history)
        # Session context: prepend the live active-workspace snapshot every turn
        # so Hermes always grounds answers in current plan state (decisions,
        # goals, overdue tasks) — not stale chat history assumptions.
        context = await _workspace_context(PLANNING_USER)
        if context:
            messages.append({"role": "user", "content": context})
        # Capability notice rides in a user-role block (system messages are
        # ignored by the api_server) so Hermes never denies its own tools.
        messages.append({"role": "user", "content": _ENV_NOTICE})
        messages.append({"role": "user", "content": message})

        payload: dict[str, Any] = {
            "model": self._settings.hermes_model,
            "messages": messages,
            "stream": False,
        }

        tracer = get_tracer("hermes_bridge")
        with tracer.start_as_current_span("hermes.submit") as span:
            span.set_attribute("agent", "hermes")
            span.set_attribute("command", "free_form")
            span.set_attribute("hermes.run_id", run_id)
            span.set_attribute("hermes.model", self._settings.hermes_model)
            span.set_attribute(LLM_REQUEST_MODEL, self._settings.hermes_model)
            span.set_attribute("hermes.chat_id", chat_id)
            span.set_attribute("hermes.history_len", len(history))

            status = "success"
            error: str | None = None
            ended_at: datetime | None = None
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                status = "timeout" if isinstance(exc, httpx.TimeoutException) else "error"
                error = f"Hermes API unreachable: {exc}"
                ended_at = datetime.now(UTC)
                span.set_attribute("hermes.status", status)
                spawn_log_run(
                    HermesRun(
                        run_id=run_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        started_at=started_at,
                        ended_at=ended_at,
                        model=self._settings.hermes_model,
                        status=status,
                        latency_ms=int((ended_at - started_at).total_seconds() * 1000),
                        error=error,
                    )
                )
                raise HermesBridgeError(error) from exc

            if resp.status_code != 200:
                status = "error"
                error = f"Hermes API returned {resp.status_code}: {resp.text[:300]}"
                ended_at = datetime.now(UTC)
                span.set_attribute("hermes.status", status)
                spawn_log_run(
                    HermesRun(
                        run_id=run_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        started_at=started_at,
                        ended_at=ended_at,
                        model=self._settings.hermes_model,
                        status=status,
                        latency_ms=int((ended_at - started_at).total_seconds() * 1000),
                        error=error,
                    )
                )
                raise HermesBridgeError(error)

            try:
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
            except (KeyError, IndexError, ValueError) as exc:
                raise HermesBridgeError(
                    f"Hermes API response malformed: {resp.text[:300]}"
                ) from exc

            if not content:
                raise HermesBridgeError("Hermes API returned empty content")

            span.set_attribute("hermes.output_len", len(content))
            span.set_attribute("hermes.status", "success")

            # §15 run-record fields available from the OpenAI-compatible
            # response: resolved model, usage tokens, finish reason.
            model = data.get("model") or self._settings.hermes_model
            usage = data.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            finish_reason = data.get("choices", [{}])[0].get("finish_reason")
            if prompt_tokens:
                span.set_attribute(LLM_USAGE_INPUT, prompt_tokens)
            if completion_tokens:
                span.set_attribute(LLM_USAGE_OUTPUT, completion_tokens)
            if finish_reason:
                span.set_attribute(LLM_RESPONSE_FINISH, finish_reason)

            ended_at = datetime.now(UTC)
            spawn_log_run(
                HermesRun(
                    run_id=run_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    started_at=started_at,
                    ended_at=ended_at,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    status=status,
                    latency_ms=int((ended_at - started_at).total_seconds() * 1000),
                    finish_reason=finish_reason,
                    final_answer=content,
                )
            )

        # Persist the exchange so a follow-up ("yes") has context.
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": content})
        self._history[chat_id] = history[-MAX_HISTORY_MESSAGES:]

        return content


_bridge: HermesBridge | None = None


def get_bridge() -> HermesBridge:
    """Return the module-level singleton bridge (history persists)."""
    global _bridge
    if _bridge is None:
        _bridge = HermesBridge()
    return _bridge
