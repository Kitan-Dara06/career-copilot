"""Planning workspace tables — Phase 2.

Implements the minimal v1 from hermes-harness-design.md §5:

  - planning_workspaces, planning_goals, planning_tasks
  - planning_decisions (with evidence + status lifecycle)
  - planning_notes
  - planning_artifacts (durable outputs)
  - planning_state (active workspace pointer per chat)
  - planning_proposals (pending writes for the inline-button confirmation flow)

Decisions folded into planning_artifacts (school_application type) to keep
the table count small for v1.

NO planning_* memory tables — conversation continuity is the workspace
summary, fetched on session bootstrap (§4 memory design).
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels: ClassVar[set[str] | None] = None
depends_on: ClassVar[set[str] | None] = None


def upgrade() -> None:
    # ── workspaces ───────────────────────────────────────────────
    op.create_table(
        "planning_workspaces",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("intake_year", sa.Integer(), nullable=False),
        sa.Column("target_degree", sa.String(50), nullable=False),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("owner", "name", name="uq_planning_workspace_owner_name"),
    )

    # ── goals ─────────────────────────────────────────────────────
    op.create_table(
        "planning_goals",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            sa.ForeignKey("planning_workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("planning_goals.id")),
        sa.Column("priority", sa.Integer(), server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_planning_goals_workspace", "planning_goals", ["workspace_id"])

    # ── tasks ─────────────────────────────────────────────────────
    op.create_table(
        "planning_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "goal_id",
            sa.BigInteger(),
            sa.ForeignKey("planning_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            sa.ForeignKey("planning_workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("due_date", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default="todo"),
        sa.Column("blocked_by_task_id", sa.BigInteger(), sa.ForeignKey("planning_tasks.id")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_planning_tasks_workspace_status",
        "planning_tasks",
        ["workspace_id", "status"],
    )

    # ── decisions ─────────────────────────────────────────────────
    op.create_table(
        "planning_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            sa.ForeignKey("planning_workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("rationale", sa.Text()),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="idea",
            comment="idea | recommendation | proposed | confirmed | superseded",
        ),
        sa.Column(
            "evidence",
            sa.JSON(),
            comment="Provenance: list of sources, retrieved_at, snippets",
        ),
        sa.Column(
            "decided_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_by_id", sa.BigInteger(), sa.ForeignKey("planning_decisions.id")),
    )
    op.create_index(
        "ix_planning_decisions_workspace_status",
        "planning_decisions",
        ["workspace_id", "status"],
    )

    # ── notes ─────────────────────────────────────────────────────
    op.create_table(
        "planning_notes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            sa.ForeignKey("planning_workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(50), nullable=False, server_default="note"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── artifacts ────────────────────────────────────────────────
    op.create_table(
        "planning_artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            sa.ForeignKey("planning_workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_planning_artifacts_workspace_type",
        "planning_artifacts",
        ["workspace_id", "type"],
    )

    # ── state (active workspace per chat) ────────────────────────
    op.create_table(
        "planning_state",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("chat_id", sa.String(64), nullable=False, unique=True),
        sa.Column("active_workspace_id", sa.BigInteger(), sa.ForeignKey("planning_workspaces.id")),
        sa.Column(
            "last_active_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── proposals (pending writes for inline-button flow) ────────
    op.create_table(
        "planning_proposals",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("chat_id", sa.String(64), nullable=False),
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            sa.ForeignKey("planning_workspaces.id", ondelete="CASCADE"),
        ),
        sa.Column("tool", sa.String(100), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "risk_level",
            sa.String(10),
            nullable=False,
            comment="low | medium | high",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
            comment="pending | approved | skipped | expired",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_planning_proposals_chat_status",
        "planning_proposals",
        ["chat_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("planning_proposals")
    op.drop_table("planning_state")
    op.drop_table("planning_artifacts")
    op.drop_table("planning_notes")
    op.drop_index("ix_planning_decisions_workspace_status", "planning_decisions")
    op.drop_table("planning_decisions")
    op.drop_index("ix_planning_tasks_workspace_status", "planning_tasks")
    op.drop_table("planning_tasks")
    op.drop_index("ix_planning_goals_workspace", "planning_goals")
    op.drop_table("planning_goals")
    op.drop_table("planning_workspaces")