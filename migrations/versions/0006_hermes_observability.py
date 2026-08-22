"""Hermes observability tables — hermes-harness-design.md §15.

Every free-form Hermes turn writes one ``hermes_runs`` row (bridge side) and
every tool invocation that reaches the career_copilot MCP server writes a
``hermes_tool_calls`` row (server side). Run-level and tool-level data are
not joinable yet — the OpenAI-compatible chat/completions response does not
expose the agent's internal tool transcript, so ``run_id`` stays NULL on
tool calls until Hermes run events are wired up (phase 4 /v1/runs events).
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels: ClassVar[set[str] | None] = None
depends_on: ClassVar[set[str] | None] = None


def upgrade() -> None:
    op.create_table(
        "hermes_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("chat_id", sa.String(100), nullable=False),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("model", sa.String(100)),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            comment="success | error | timeout",
        ),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("finish_reason", sa.String(30)),
        sa.Column("final_answer", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("extra_metadata", sa.dialects.postgresql.JSONB()),
    )
    op.create_index(
        "idx_hermes_runs_lookup",
        "hermes_runs",
        ["user_id", "started_at"],
    )
    op.create_index(
        "idx_hermes_runs_chat",
        "hermes_runs",
        ["chat_id", "started_at"],
    )

    op.create_table(
        "hermes_tool_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.String(64)),
        sa.Column("chat_id", sa.String(100)),
        sa.Column("tool_name", sa.String(200), nullable=False),
        sa.Column("args", sa.dialects.postgresql.JSONB()),
        sa.Column("output_excerpt", sa.Text()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column(
            "outcome",
            sa.String(20),
            nullable=False,
            comment="success | error",
        ),
        sa.Column(
            "ts",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_hermes_tool_calls_run_id",
        "hermes_tool_calls",
        ["run_id"],
    )
    op.create_index(
        "idx_hermes_tool_calls_lookup",
        "hermes_tool_calls",
        ["tool_name", "ts"],
    )


def downgrade() -> None:
    op.drop_index("idx_hermes_tool_calls_lookup", "hermes_tool_calls")
    op.drop_index("ix_hermes_tool_calls_run_id", "hermes_tool_calls")
    op.drop_table("hermes_tool_calls")
    op.drop_index("idx_hermes_runs_chat", "hermes_runs")
    op.drop_index("idx_hermes_runs_lookup", "hermes_runs")
    op.drop_table("hermes_runs")