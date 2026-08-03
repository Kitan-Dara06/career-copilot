"""Add Job Hunter tables: openings, opening_status, digests.

Mirrors the Paper Tracker pattern (immutable source record + per-user status + digest log).
Dedup key: job_hunter_openings.external_id (gh:12345, lever:abc-def, url:<sha8>).

Job Hunter's tables follow the §12 schema of job-hunter-design (1).md, with
two additions learned from the Paper Tracker code review:
  - ``external_id`` is UNIQUE so re-discovery across digest cadences doesn't
    double-insert.
  - opening_status split into its own table (instead of a.STATUS column on
    job_hunter_openings) so multi-user queries are joins on integer IDs.

NO cover-letter / draft tables in v0.1 — cover-letter flow is v0.2 scope per
the design review Jan 2026; deferring until after the approval-gate decision
(inline-Telegram vs email; see job-hunter-design (1).md §14).
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels: ClassVar[set[str] | None] = None
depends_on: ClassVar[set[str] | None] = None


def upgrade() -> None:
    """Create Job Hunter v0.1 tables."""
    op.create_table(
        "job_hunter_openings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("external_id", sa.String(255), unique=True, nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("organization", sa.String(500), nullable=False),
        sa.Column("team", sa.String(255)),
        sa.Column("role_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required_skills", sa.dialects.postgresql.JSONB()),
        sa.Column("nice_to_have", sa.dialects.postgresql.JSONB()),
        sa.Column("location", sa.String(500)),
        sa.Column("remote_ok", sa.Boolean()),
        sa.Column("deadline", sa.TIMESTAMP(timezone=True)),
        sa.Column("application_url", sa.Text()),
        sa.Column("posted_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "discovered_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("salary_min", sa.Integer()),
        sa.Column("salary_max", sa.Integer()),
        sa.Column("salary_currency", sa.String(10)),
        sa.Column("visa_status", sa.String(20)),
        sa.Column("region", sa.String(50), nullable=False),
        sa.Column("raw_html", sa.Text()),
    )
    op.create_index(
        "idx_jh_opening_region_posted", "job_hunter_openings", ["region", "posted_at"]
    )
    op.create_index("idx_jh_opening_org", "job_hunter_openings", ["organization"])

    op.create_table(
        "job_hunter_opening_status",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "opening_id",
            sa.BigInteger(),
            sa.ForeignKey("job_hunter_openings.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'new'")),
        sa.Column("match_score", sa.Numeric(5, 4)),
        sa.Column("feedback", sa.String(50)),
        sa.Column("shown_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("saved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("skipped_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_jh_status_user_opening",
        "job_hunter_opening_status",
        ["user_id", "opening_id"],
        unique=True,
    )
    op.create_index(
        "idx_jh_status_user_status",
        "job_hunter_opening_status",
        ["user_id", "status"],
    )

    op.create_table(
        "job_hunter_digests",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "sent_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("openings_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("extra_metadata", sa.dialects.postgresql.JSONB()),
    )


def downgrade() -> None:
    """Drop Job Hunter v0.1 tables."""
    op.drop_table("job_hunter_digests")
    op.drop_table("job_hunter_opening_status")
    op.drop_index("idx_jh_opening_org", "job_hunter_openings")
    op.drop_index("idx_jh_opening_region_posted", "job_hunter_openings")
    op.drop_index("idx_jh_status_user_status", "job_hunter_opening_status")
    op.drop_index("idx_jh_status_user_opening", "job_hunter_opening_status")
    op.drop_table("job_hunter_openings")