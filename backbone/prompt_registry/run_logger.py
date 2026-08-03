"""Run logger — persists every LLM call to the ``prompt_runs`` table.

Allows querying by agent/prompt and computing cost summaries.
No model calls happen here — this is purely a logging layer.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import text

from backbone.db.session import async_session_factory


class PromptRun(BaseModel):
    """A single LLM call, to be logged to the ``prompt_runs`` table."""

    agent: str
    prompt_name: str
    prompt_version: int
    model: str
    input_hash: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    output: str | None = None
    cost_usd: float | None = None
    metadata: dict[str, object] | None = None


class ModelPricing(BaseModel):
    """Per-model token pricing (used for cost calculation)."""

    input_per_1m: float = 0.0
    output_per_1m: float = 0.0


_DEFAULT_PRICING: dict[str, ModelPricing] = {
    "gemini-2.5-flash": ModelPricing(input_per_1m=0.10, output_per_1m=0.40),
    "gpt-4o-mini": ModelPricing(input_per_1m=0.15, output_per_1m=0.60),
    "claude-3-haiku": ModelPricing(input_per_1m=0.25, output_per_1m=1.25),
    "": ModelPricing(),
}


def _compute_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float:
    pricing = _DEFAULT_PRICING.get(model, ModelPricing())
    total = 0.0
    if input_tokens:
        total += (input_tokens / 1_000_000) * pricing.input_per_1m
    if output_tokens:
        total += (output_tokens / 1_000_000) * pricing.output_per_1m
    return round(total, 6)


class CostSummary(BaseModel):
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    run_count: int = 0
    by_prompt: dict[str, float] = {}


class PromptRunLogger:
    """Logger for LLM prompt runs."""

    def __init__(self) -> None:
        self._factory = async_session_factory()

    async def log(self, run: PromptRun) -> None:
        """Persist a prompt run to the database."""
        cost = run.cost_usd
        if cost is None:
            cost = _compute_cost(run.model, run.input_tokens, run.output_tokens)

        async with self._factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO prompt_runs
                        (agent, prompt_name, prompt_version, model,
                         input_hash, input_tokens, output_tokens,
                         latency_ms, output, cost_usd, extra_metadata)
                    VALUES
                        (:agent, :prompt_name, :prompt_version, :model,
                         :input_hash, :input_tokens, :output_tokens,
                         :latency_ms, :output, :cost_usd,
                         :extra_metadata)
                    """
                ),
                {
                    "agent": run.agent,
                    "prompt_name": run.prompt_name,
                    "prompt_version": run.prompt_version,
                    "model": run.model,
                    "input_hash": run.input_hash,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "latency_ms": run.latency_ms,
                    "output": run.output,
                    "cost_usd": cost,
                    "extra_metadata": json.dumps(run.metadata) if run.metadata else None,
                },
            )
            await session.commit()

    async def query(
        self,
        agent: str,
        prompt_name: str | None = None,
        since: datetime | None = None,
    ) -> list[PromptRun]:
        """Query prompt runs by agent, name, and/or time."""
        conditions = ["agent = :agent"]
        params: dict[str, object] = {"agent": agent}

        if prompt_name:
            conditions.append("prompt_name = :prompt_name")
            params["prompt_name"] = prompt_name

        if since:
            conditions.append("ts >= :since")
            params["since"] = since

        where = " AND ".join(conditions)
        query = text(
            f"""
            SELECT agent, prompt_name, prompt_version, model,
                   input_hash, input_tokens, output_tokens,
                   latency_ms, output, cost_usd
            FROM prompt_runs
            WHERE {where}
            ORDER BY ts DESC
            """
        )

        async with self._factory() as session:
            result = await session.execute(query, params)
            rows = result.all()

        return [
            PromptRun(
                agent=row.agent,
                prompt_name=row.prompt_name,
                prompt_version=row.prompt_version,
                model=row.model,
                input_hash=row.input_hash,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                latency_ms=row.latency_ms,
                output=row.output,
                cost_usd=row.cost_usd,
            )
            for row in rows
        ]

    async def cost_summary(self, agent: str, since: datetime | None = None) -> CostSummary:
        """Compute a cost summary for an agent since a given time."""
        runs = await self.query(agent, since=since)
        summary = CostSummary(
            total_cost_usd=sum(r.cost_usd or 0 for r in runs),
            total_input_tokens=sum(r.input_tokens or 0 for r in runs),
            total_output_tokens=sum(r.output_tokens or 0 for r in runs),
            run_count=len(runs),
        )
        for r in runs:
            summary.by_prompt[r.prompt_name] = summary.by_prompt.get(r.prompt_name, 0) + (
                r.cost_usd or 0
            )
        return summary
