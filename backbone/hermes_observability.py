"""Hermes observability — persists to ``hermes_runs`` and ``hermes_tool_calls``.

Implements hermes-harness-design.md §15:

- ``hermes_runs``      — one row per free-form turn, written by the bridge
                         (``career_copilot.hermes_bridge.submit``).
- ``hermes_tool_calls`` — one row per tool invocation that reaches the
                         career_copilot MCP server (``backbone.mcp.server``).

Writers are called fire-and-forget so a logging failure never breaks the
chat flow. This module is pure persistence (no model calls), mirroring the
``prompt_runs`` logger pattern.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from backbone.db.session import async_session_factory
from backbone.prompt_registry.run_logger import _compute_cost

logger = logging.getLogger("hermes_observability")

# Maximum characters persisted for args / output excerpts (dashboard value
# without blowing up the row size).
_MAX_EXCERPT_CHARS = 800
_MAX_ARGS_CHARS = 2000


class HermesRun(BaseModel):
    """A single Hermes conversation turn, persisted to ``hermes_runs``."""

    run_id: str
    user_id: str
    chat_id: str
    started_at: datetime
    ended_at: datetime | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    status: str = "success"  # success | error | timeout
    latency_ms: int | None = None
    finish_reason: str | None = None
    final_answer: str | None = None
    error: str | None = None
    metadata: dict[str, object] | None = None


class HermesToolCall(BaseModel):
    """A single tool invocation made through the career_copilot MCP server."""

    tool_name: str
    args: dict[str, object] | None = None
    output_excerpt: str | None = None
    latency_ms: int | None = None
    outcome: str = "success"  # success | error
    run_id: str | None = None
    chat_id: str | None = None


def summarize(value: object, *, limit: int = _MAX_EXCERPT_CHARS) -> str | None:
    """Render an arbitrary tool result as a short text excerpt."""
    if value is None:
        return None
    try:
        text_value = json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text_value = str(value)
    if len(text_value) > limit:
        text_value = text_value[:limit] + "…[truncated]"
    return text_value


def summarize_args(args: dict[str, object] | None) -> dict[str, object] | None:
    """Truncate long string arguments so a bad query can't bloat the row."""
    if not args:
        return args or None
    capped: dict[str, object] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 400:
            capped[key] = value[:400] + "…[truncated]"
        else:
            capped[key] = value
    return capped


class HermesRunLogger:
    """Logger for Hermes runs and MCP tool calls."""

    def __init__(self, factory: Any = None) -> None:
        # ``factory`` is injectable for tests (fresh engine per event loop);
        # production callers use the module-level cached factory.
        self._factory = factory or async_session_factory()

    async def log_run(self, run: HermesRun) -> None:
        """Persist one Hermes run row."""
        cost = run.cost_usd
        if cost is None:
            cost = _compute_cost(run.model or "", run.prompt_tokens, run.completion_tokens)

        async with self._factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO hermes_runs
                        (run_id, user_id, chat_id, started_at, ended_at,
                         model, prompt_tokens, completion_tokens, total_tokens,
                         cost_usd, status, latency_ms, finish_reason,
                         final_answer, error, extra_metadata)
                    VALUES
                        (:run_id, :user_id, :chat_id, :started_at, :ended_at,
                         :model, :prompt_tokens, :completion_tokens, :total_tokens,
                         :cost_usd, :status, :latency_ms, :finish_reason,
                         :final_answer, :error, :extra_metadata)
                    """
                ),
                {
                    "run_id": run.run_id,
                    "user_id": run.user_id,
                    "chat_id": run.chat_id,
                    "started_at": run.started_at,
                    "ended_at": run.ended_at,
                    "model": run.model,
                    "prompt_tokens": run.prompt_tokens,
                    "completion_tokens": run.completion_tokens,
                    "total_tokens": run.total_tokens,
                    "cost_usd": cost,
                    "status": run.status,
                    "latency_ms": run.latency_ms,
                    "finish_reason": run.finish_reason,
                    "final_answer": run.final_answer,
                    "error": run.error,
                    "extra_metadata": json.dumps(run.metadata) if run.metadata else None,
                },
            )
            await session.commit()

    async def log_tool_call(self, call: HermesToolCall) -> None:
        """Persist one MCP tool-call row."""
        async with self._factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO hermes_tool_calls
                        (run_id, chat_id, tool_name, args, output_excerpt,
                         latency_ms, outcome)
                    VALUES
                        (:run_id, :chat_id, :tool_name, :args, :output_excerpt,
                         :latency_ms, :outcome)
                    """
                ),
                {
                    "run_id": call.run_id,
                    "chat_id": call.chat_id,
                    "tool_name": call.tool_name,
                    "args": json.dumps(call.args) if call.args else None,
                    "output_excerpt": call.output_excerpt,
                    "latency_ms": call.latency_ms,
                    "outcome": call.outcome,
                },
            )
            await session.commit()

    async def query_runs(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[HermesRun]:
        """Query recent runs, newest first (read side for dashboards)."""
        conditions: list[str] = []
        params: dict[str, object] = {"limit": limit}
        if user_id:
            conditions.append("user_id = :user_id")
            params["user_id"] = user_id
        if status:
            conditions.append("status = :status")
            params["status"] = status
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        async with self._factory() as session:
            result = await session.execute(
                text(
                    f"""
                    SELECT run_id, user_id, chat_id, started_at, ended_at,
                           model, prompt_tokens, completion_tokens, total_tokens,
                           cost_usd, status, latency_ms, finish_reason,
                           final_answer, error
                    FROM hermes_runs
                    {where}
                    ORDER BY started_at DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
            rows = result.all()

        return [
            HermesRun(
                run_id=r.run_id,
                user_id=r.user_id,
                chat_id=r.chat_id,
                started_at=r.started_at,
                ended_at=r.ended_at,
                model=r.model,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
                total_tokens=r.total_tokens,
                cost_usd=float(r.cost_usd) if r.cost_usd is not None else None,
                status=r.status,
                latency_ms=r.latency_ms,
                finish_reason=r.finish_reason,
                final_answer=r.final_answer,
                error=r.error,
            )
            for r in rows
        ]

    async def query_tool_calls(
        self,
        *,
        tool_name: str | None = None,
        outcome: str | None = None,
        limit: int = 20,
    ) -> list[HermesToolCall]:
        """Query recent MCP tool calls, newest first."""
        conditions: list[str] = []
        params: dict[str, object] = {"limit": limit}
        if tool_name:
            conditions.append("tool_name = :tool_name")
            params["tool_name"] = tool_name
        if outcome:
            conditions.append("outcome = :outcome")
            params["outcome"] = outcome
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        async with self._factory() as session:
            result = await session.execute(
                text(
                    f"""
                    SELECT run_id, chat_id, tool_name, args, output_excerpt,
                           latency_ms, outcome, ts
                    FROM hermes_tool_calls
                    {where}
                    ORDER BY ts DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
            rows = result.all()

        return [
            HermesToolCall(
                run_id=r.run_id,
                chat_id=r.chat_id,
                tool_name=r.tool_name,
                args=r.args,
                output_excerpt=r.output_excerpt,
                latency_ms=r.latency_ms,
                outcome=r.outcome,
            )
            for r in rows
        ]


def spawn_log_run(run: HermesRun) -> None:
    """Fire-and-forget persist of a run — never raises into the caller."""

    async def _do() -> None:
        try:
            await HermesRunLogger().log_run(run)
        except Exception:  # noqa: BLE001 — logging must never break the chat
            logger.exception("hermes_run_persist_failed", run_id=run.run_id)

    _spawn(_do())


def spawn_log_tool_call(call: HermesToolCall) -> None:
    """Fire-and-forget persist of a tool call — never raises into the caller."""

    async def _do() -> None:
        try:
            await HermesRunLogger().log_tool_call(call)
        except Exception:  # noqa: BLE001
            logger.exception("hermes_tool_call_persist_failed", tool=call.tool_name)

    _spawn(_do())


def _spawn(coro: object) -> None:
    import asyncio

    try:
        asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
    except RuntimeError:
        # No running loop (e.g. module imported outside a server) — drop it.
        pass