"""Initial v0.1 schema — all structured tables.

Vector storage note:
    Vectors are stored in Qdrant Cloud, not PostgreSQL.
    Tables that would have VECTOR(1024) columns (interest_vectors,
    professor_interest_vectors) store Qdrant point IDs as TEXT instead.
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels: ClassVar[set[str] | None] = None
depends_on: ClassVar[set[str] | None] = None


def upgrade() -> None:
    """Create all v0.1 tables."""
    # ── User facts ──
    op.create_table(
        "user_facts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("key", sa.String(255), unique=True, nullable=False),
        sa.Column("value", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── Interest vectors (metadata; actual vectors in Qdrant) ──
    op.create_table(
        "interest_vectors",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("qdrant_id", sa.String(255), nullable=False, comment="Qdrant point ID"),
        sa.Column("source", sa.String(50), nullable=False, comment="'seed' | 'retune'"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── Short-term memory ──
    op.create_table(
        "short_term_memory",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("namespace", sa.String(255), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_short_term_namespace_key", "short_term_memory", ["namespace", "key"])
    op.create_index("idx_short_term_expires", "short_term_memory", ["expires_at"])

    # ── Professors ──
    op.create_table(
        "professors",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("affiliation", sa.String(500), nullable=True),
        sa.Column("homepage_url", sa.String(1000), nullable=True),
        sa.Column("arxiv_author", sa.String(255), nullable=True),
        sa.Column(
            "added_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    # ── Professor papers ──
    op.create_table(
        "professor_papers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("professor_id", sa.BigInteger(), sa.ForeignKey("professors.id"), nullable=False),
        sa.Column("arxiv_id", sa.String(50), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("authors", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("shown_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("feedback", sa.String(20), nullable=True, comment="'read' | 'saved' | 'skipped'"),
    )
    op.create_index(
        "idx_prof_paper_lookup", "professor_papers", ["professor_id", "arxiv_id"], unique=True
    )

    # ── Professor interest vectors (metadata; vectors in Qdrant) ──
    op.create_table(
        "professor_interest_vectors",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("professor_id", sa.BigInteger(), sa.ForeignKey("professors.id"), nullable=False),
        sa.Column("qdrant_id", sa.String(255), nullable=False, comment="Qdrant point ID"),
        sa.Column("source", sa.String(50), nullable=False, comment="'seed' | 'retune'"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── Digests ──
    op.create_table(
        "digests",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("mode", sa.String(20), nullable=False, comment="'daily' | 'weekly'"),
        sa.Column(
            "sent_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("items_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("extra_metadata", sa.dialects.postgresql.JSONB(), nullable=True),
    )

    # ── Digest items ──
    op.create_table(
        "digest_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("digest_id", sa.BigInteger(), sa.ForeignKey("digests.id"), nullable=False),
        sa.Column("stream", sa.String(20), nullable=False, comment="'interest' | 'professor'"),
        sa.Column("professor_id", sa.BigInteger(), sa.ForeignKey("professors.id"), nullable=True),
        sa.Column("arxiv_id", sa.String(50), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("authors", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column(
            "shown_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── Feedback log ──
    op.create_table(
        "feedback_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("item_id", sa.String(255), nullable=False),
        sa.Column(
            "signal",
            sa.String(20),
            nullable=False,
            comment="'read' | 'save' | 'skip' | 'more' | 'less'",
        ),
        sa.Column("stream", sa.String(20), nullable=True, comment="'interest' | 'professor'"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_feedback_item", "feedback_log", ["item_id"])

    # ── Pending email drafts ──
    op.create_table(
        "pending_drafts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("draft_id", sa.String(255), unique=True, nullable=False),
        sa.Column("recipient", sa.String(500), nullable=False),
        sa.Column("subject", sa.String(1000), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("metadata", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # ── Scheduled jobs ──
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("job_id", sa.String(255), unique=True, nullable=False),
        sa.Column("job_name", sa.String(255), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── Long-term version tracking (for long_term.py / Qdrant) ──
    op.create_table(
        "long_term_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("namespace", sa.String(255), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("qdrant_id", sa.String(500), nullable=False, comment="Qdrant point ID"),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_lt_version_lookup",
        "long_term_versions",
        ["namespace", "key", "version"],
        unique=True,
    )
    op.create_index(
        "idx_lt_version_active",
        "long_term_versions",
        ["namespace", "key", "is_active"],
    )

    # ── Prompt runs ──
    op.create_table(
        "prompt_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "ts",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("agent", sa.String(100), nullable=False),
        sa.Column("prompt_name", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("extra_metadata", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.create_index("idx_prompt_runs_lookup", "prompt_runs", ["agent", "prompt_name", "ts"])


def downgrade() -> None:
    """Drop all v0.1 tables (reverse order for FK safety)."""
    op.drop_table("prompt_runs")
    op.drop_table("long_term_versions")
    op.drop_table("scheduled_jobs")
    op.drop_table("pending_drafts")
    op.drop_table("feedback_log")
    op.drop_table("digest_items")
    op.drop_table("digests")
    op.drop_table("professor_interest_vectors")
    op.drop_table("professor_papers")
    op.drop_table("professors")
    op.drop_table("short_term_memory")
    op.drop_table("interest_vectors")
    op.drop_table("user_facts")
