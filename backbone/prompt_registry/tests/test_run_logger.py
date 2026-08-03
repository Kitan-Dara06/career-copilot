"""Tests for prompt run logger."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backbone.prompt_registry.run_logger import (
    PromptRun,
    PromptRunLogger,
)


@pytest.fixture
def logger() -> PromptRunLogger:
    return PromptRunLogger()


@pytest.mark.asyncio
async def test_log_and_query(logger: PromptRunLogger) -> None:
    """Log a run then query it back."""
    run = PromptRun(
        agent="paper_tracker",
        prompt_name="why_relevant",
        prompt_version=1,
        model="hermes-2-pro",
        input_hash="abc123",
        input_tokens=100,
        output_tokens=50,
        latency_ms=500,
        output="REFUSED",
    )
    await logger.log(run)

    runs = await logger.query("paper_tracker", prompt_name="why_relevant")
    assert len(runs) >= 1
    assert runs[0].agent == "paper_tracker"
    assert runs[0].prompt_name == "why_relevant"


@pytest.mark.asyncio
async def test_cost_summary(logger: PromptRunLogger) -> None:
    """Cost summary aggregates correctly."""
    run1 = PromptRun(
        agent="paper_tracker",
        prompt_name="why_relevant",
        prompt_version=1,
        model="hermes-2-pro",
        input_hash="h1",
        input_tokens=1_000_000,
        output_tokens=200_000,
    )
    await logger.log(run1)

    summary = await logger.cost_summary(
        "paper_tracker",
        since=datetime.now(UTC).replace(year=2020),
    )
    assert summary.run_count >= 1
    assert summary.total_input_tokens >= 1_000_000
    assert summary.by_prompt.get("why_relevant", 0) > 0


@pytest.mark.asyncio
async def test_log_without_tokens(logger: PromptRunLogger) -> None:
    """Logging without token counts computes a cost of 0."""
    from datetime import datetime

    run = PromptRun(
        agent="paper_tracker",
        prompt_name=f"no_tokens_{datetime.now(UTC).timestamp()}",
        prompt_version=1,
        model="unknown-model",
        input_hash="h2",
    )
    await logger.log(run)

    runs = await logger.query("paper_tracker", prompt_name=run.prompt_name)
    assert len(runs) == 1
    assert runs[0].cost_usd == 0.0
