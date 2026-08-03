"""Add Contribution Finder tables: opportunities, feedback, repos.

v0.1 tables per §9 of contribution-finder-design.md.
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels: ClassVar[set[str] | None] = None
depends_on: ClassVar[set[str] | None] = None


def upgrade() -> None:
    op.create_table(
        "contribution_opportunities",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("github_repo", sa.String(255), nullable=False),
        sa.Column("github_issue_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_snippet", sa.Text()),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("labels", sa.String(500)),
        sa.Column("created_at_gh", sa.TIMESTAMP(timezone=True)),
        sa.Column("updated_at_gh", sa.TIMESTAMP(timezone=True)),
        sa.Column("comment_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("reaction_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("age_days", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("linked_pr_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_activity_days", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("skill_match", sa.Numeric(5, 4)),
        sa.Column("problem", sa.Text()),
        sa.Column("why_it_matters", sa.Text()),
        sa.Column("suggested_first_steps", sa.Text()),
        sa.Column("estimated_effort", sa.String(20)),
        sa.Column("blocked_by", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'new'")),
        sa.Column(
            "first_seen_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "last_seen_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "idx_cf_opp_repo_issue", "contribution_opportunities",
        ["github_repo", "github_issue_number"], unique=True,
    )
    op.create_index("idx_cf_opp_status", "contribution_opportunities", ["status"])
    op.create_index("idx_cf_opp_score", "contribution_opportunities", ["score"])

    op.create_table(
        "contribution_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "opportunity_id", sa.BigInteger(),
            sa.ForeignKey("contribution_opportunities.id"), nullable=False,
        ),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("signal", sa.String(20), nullable=False),
        sa.Column(
            "feedback_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "idx_cf_feedback_user_opp", "contribution_feedback",
        ["user_id", "opportunity_id"],
    )

    op.create_table(
        "contribution_repos",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("github_full_name", sa.String(255), unique=True, nullable=False),
        sa.Column("language", sa.String(50), nullable=False, server_default=sa.text("'python'")),
        sa.Column("topic_hint", sa.String(100)),
        sa.Column(
            "added_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("contribution_feedback")
    op.drop_table("contribution_opportunities")
    op.drop_table("contribution_repos")
