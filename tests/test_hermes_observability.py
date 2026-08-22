"""Tests for the Hermes observability logger (hermes_runs / hermes_tool_calls)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backbone.db.session import async_session_factory, reset_session_cache
from backbone.hermes_observability import (
    HermesRun,
    HermesRunLogger,
    HermesToolCall,
    summarize,
    summarize_args,
)
from career_copilot.config import get_settings


@pytest.fixture
def logger() -> HermesRunLogger:
    # Fresh engine per test (bypasses the module-level cache) so asyncpg
    # connections stay bound to this test's event loop — see session.py.
    reset_session_cache()
    factory: async_sessionmaker[AsyncSession] = async_session_factory(
        settings=get_settings()
    )
    return HermesRunLogger(factory=factory)


@pytest.mark.asyncio
async def test_log_and_query_run(logger: HermesRunLogger) -> None:
    """A run round-trips through hermes_runs."""
    now = datetime.now(UTC)
    run_id = f"test-run-{uuid4().hex[:8]}"
    run = HermesRun(
        run_id=run_id,
        user_id="aaliyah",
        chat_id="chat-1",
        started_at=now,
        ended_at=now + timedelta(seconds=2),
        model="gemini-2.5-flash-lite",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        status="success",
        latency_ms=2000,
        finish_reason="stop",
        final_answer="Top 3 clusters: rag, agents, llm_ops.",
    )
    await logger.log_run(run)

    rows = await logger.query_runs(limit=10)
    match = next(r for r in rows if r.run_id == run_id)
    assert match.chat_id == "chat-1"
    assert match.model == "gemini-2.5-flash-lite"
    assert match.total_tokens == 150
    assert match.status == "success"
    assert "rag" in (match.final_answer or "")


@pytest.mark.asyncio
async def test_log_and_query_tool_call(logger: HermesRunLogger) -> None:
    """A tool call round-trips through hermes_tool_calls."""
    call = HermesToolCall(
        tool_name="career.professors.search",
        args={"institution": "McGill", "topic": "retrieval"},
        output_excerpt='{"professors": [{"name": "Rabbany"}]}',
        latency_ms=1234,
        outcome="success",
    )
    await logger.log_tool_call(call)

    rows = await logger.query_tool_calls(tool_name="career.professors.search", limit=10)
    match = next(r for r in rows if r.tool_name == "career.professors.search")
    assert match.latency_ms == 1234
    assert match.outcome == "success"
    assert match.args == {"institution": "McGill", "topic": "retrieval"}


@pytest.mark.asyncio
async def test_error_run_is_queryable(logger: HermesRunLogger) -> None:
    """Failed runs are persisted with status + error text."""
    now = datetime.now(UTC)
    run_id = f"test-run-{uuid4().hex[:8]}"
    await logger.log_run(
        HermesRun(
            run_id=run_id,
            user_id="aaliyah",
            chat_id="chat-1",
            started_at=now,
            ended_at=now,
            status="timeout",
            error="Hermes API unreachable: timed out",
        )
    )
    rows = await logger.query_runs(status="timeout", limit=10)
    assert any(r.run_id == run_id for r in rows)


def test_summarize_truncates_long_results() -> None:
    """Summaries are capped so a huge tool result can't bloat the row."""
    long = summarize({"data": "x" * 5000})
    assert long is not None
    assert len(long) <= 900
    assert "truncated" in long


def test_summarize_args_caps_long_strings() -> None:
    capped = summarize_args({"query": "q" * 1000, "ok": "fine"})
    assert capped is not None
    assert capped["ok"] == "fine"
    assert len(capped["query"]) < 500  # type: ignore[arg-type]
    assert summarize_args(None) is None