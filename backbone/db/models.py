"""SQLAlchemy ORM models for all v0.1 tables.

Vector storage note:
    Vectors are stored in **Qdrant Cloud**, not in PostgreSQL.
    PostgreSQL tables store Qdrant point IDs (``qdrant_id`` columns)
    and associated metadata where the design doc specifies vector columns.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class UserFact(Base):
    """Durable user facts (research interests, preferences, writing style profile)."""

    __tablename__ = "user_facts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class InterestVector(Base):
    """Tracks interest vector metadata; the actual 1024-dim vector lives in Qdrant."""

    __tablename__ = "interest_vectors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    qdrant_id: Mapped[str] = mapped_column(String(255), nullable=False, comment="Qdrant point ID")
    source: Mapped[str] = mapped_column(String(50), nullable=False, comment="'seed' | 'retune'")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class ShortTermMemory(Base):
    """Short-term memory with TTL. Stores JSON-serializable values."""

    __tablename__ = "short_term_memory"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        Index("idx_short_term_namespace_key", "namespace", "key"),
        Index("idx_short_term_expires", "expires_at"),
    )


class Professor(Base):
    """Professor watchlist — academics the user is tracking."""

    __tablename__ = "professors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    affiliation: Mapped[str | None] = mapped_column(String(500))
    homepage_url: Mapped[str | None] = mapped_column(String(1000))
    arxiv_author: Mapped[str | None] = mapped_column(String(255))
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    papers: Mapped[list[ProfessorPaper]] = relationship(back_populates="professor")
    interest_vectors: Mapped[list[ProfessorInterestVector]] = relationship(
        back_populates="professor"
    )


class ProfessorPaper(Base):
    """Papers published by a watched professor, tracked for the digest."""

    __tablename__ = "professor_papers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    professor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("professors.id"), nullable=False
    )
    arxiv_id: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    authors: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    shown_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    why: Mapped[str | None] = mapped_column(Text)
    feedback: Mapped[str | None] = mapped_column(String(20), comment="'read' | 'saved' | 'skipped'")

    professor: Mapped[Professor] = relationship(back_populates="papers")

    __table_args__ = (Index("idx_prof_paper_lookup", "professor_id", "arxiv_id", unique=True),)


class ProfessorInterestVector(Base):
    """Per-professor direction vector metadata; actual vector in Qdrant."""

    __tablename__ = "professor_interest_vectors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    professor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("professors.id"), nullable=False
    )
    qdrant_id: Mapped[str] = mapped_column(String(255), nullable=False, comment="Qdrant point ID")
    source: Mapped[str] = mapped_column(String(50), nullable=False, comment="'seed' | 'retune'")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )

    professor: Mapped[Professor] = relationship(back_populates="interest_vectors")


class Digest(Base):
    """A sent digest (daily or weekly)."""

    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, comment="'daily' | 'weekly'")
    sent_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    items_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    items: Mapped[list[DigestItem]] = relationship(back_populates="digest")


class DigestItem(Base):
    """A single paper in a digest."""

    __tablename__ = "digest_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    digest_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("digests.id"), nullable=False)
    stream: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="'interest' | 'professor'"
    )
    professor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("professors.id"))
    arxiv_id: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    authors: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    why: Mapped[str | None] = mapped_column(Text)
    shown_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )

    digest: Mapped[Digest] = relationship(back_populates="items")


class FeedbackLog(Base):
    """User feedback signals on digest items."""

    __tablename__ = "feedback_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    signal: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="'read' | 'save' | 'skip' | 'more' | 'less'"
    )
    stream: Mapped[str | None] = mapped_column(String(20), comment="'interest' | 'professor'")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )

    __table_args__ = (Index("idx_feedback_item", "item_id"),)


class LongTermVersion(Base):
    """Version tracking for long-term memory records stored in Qdrant."""

    __tablename__ = "long_term_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    qdrant_id: Mapped[str] = mapped_column(String(500), nullable=False, comment="Qdrant point ID")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        Index(
            "idx_lt_version_lookup",
            "namespace",
            "key",
            "version",
            unique=True,
        ),
        Index("idx_lt_version_active", "namespace", "key", "is_active"),
    )


class PendingDraft(Base):
    """Queued email drafts awaiting user approval."""

    __tablename__ = "pending_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    draft_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    recipient: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str] = mapped_column(String(1000), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    extra_metadata: Mapped[dict[str, object] | None] = mapped_column("metadata", JSONB)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
        comment="'pending' | 'sent' | 'cancelled'",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class ScheduledJob(Base):
    """Persisted scheduled jobs for the background worker."""

    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class PromptRun(Base):
    """Every LLM call is logged here for audit, cost tracking, and prompt eval."""

    __tablename__ = "prompt_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    agent: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    output: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))
    extra_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    __table_args__ = (Index("idx_prompt_runs_lookup", "agent", "prompt_name", "ts"),)


# ── Job Hunter tables (§12 of job-hunter-design) ─────────────────────────────


class JobOpening(Base):
    """A discovered job posting — the immutable source record.

    Per-section layers mirror Paper Tracker's arxiv_id-based dedup. We dedup
    by ``external_id`` (e.g. ``gh:12345``, ``lever:abc-def``, ``url:<sha8>``);
    re-discovering the same posting across digest cadences doesn't re-insert.
    Vectors (1024-dim Voyage) live in Qdrant under namespace ``job_hunter/openings``.
    """

    __tablename__ = "job_hunter_openings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False, comment="'greenhouse' | 'lever' | 'ashby' | 'firecrawl' | 'tavily' | 'manual'")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    organization: Mapped[str] = mapped_column(String(500), nullable=False)
    team: Mapped[str | None] = mapped_column(String(255))
    role_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="'internship' | 'co_op' | 'new_grad' | 'research' | 'unknown'")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_skills: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    nice_to_have: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    location: Mapped[str | None] = mapped_column(String(500))
    remote_ok: Mapped[bool | None] = mapped_column(Boolean)
    deadline: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    application_url: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    salary_min: Mapped[int | None] = mapped_column(Integer, comment="in posting's currency (see salary_currency)")
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(10), comment="'NGN','USD','EUR','CAD'")
    visa_status: Mapped[str | None] = mapped_column(String(20), comment="'yes' | 'no' | 'unknown'")
    region: Mapped[str] = mapped_column(String(50), nullable=False, comment="'nigeria' | 'africa' | 'eu' | 'canada' | 'international_remote'")
    raw_html: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_jh_opening_region_posted", "region", "posted_at"),
        Index("idx_jh_opening_org", "organization"),
    )


class JobOpeningStatus(Base):
    """Per-user per-opening status — the seen/saved/skipped/feedback layer.

    Paper Tracker used a single ``feedback_log`` table for this; we follow the
    same pattern to keep Paper Tracker's existing ``memory.feedback`` tool
    reusable, but mirror to a per-opening status table so Job Hunter queries
    are joins on stable integer IDs (not loose item_id strings).
    """

    __tablename__ = "job_hunter_opening_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    opening_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("job_hunter_openings.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'new'"),
        comment="'new' | 'shown' | 'saved' | 'skipped' | 'discarded'"
    )
    match_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    feedback: Mapped[str | None] = mapped_column(String(50))
    shown_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    saved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    skipped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )

    __table_args__ = (
        Index("idx_jh_status_user_opening", "user_id", "opening_id", unique=True),
        Index("idx_jh_status_user_status", "user_id", "status"),
    )


class JobHunterDigest(Base):
    """A sent Job Hunter digest, for audit and "show what we sent" reports."""

    __tablename__ = "job_hunter_digests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    openings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    __table_args__ = (Index("idx_jh_digest_sent", "sent_at"),)


# ── Contribution Finder tables (§9 of contribution-finder-design) ────────────


class ContributionOpportunity(Base):
    """A discovered GitHub contribution opportunity."""

    __tablename__ = "contribution_opportunities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    github_repo: Mapped[str] = mapped_column(String(255), nullable=False)
    github_issue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body_snippet: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    labels: Mapped[str | None] = mapped_column(String(500))
    created_at_gh: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    updated_at_gh: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    linked_pr_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="PRs referencing this issue")
    last_activity_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    skill_match: Mapped[float | None] = mapped_column(Numeric(5, 4))
    problem: Mapped[str | None] = mapped_column(Text, comment="Gemini analysis")
    why_it_matters: Mapped[str | None] = mapped_column(Text)
    suggested_first_steps: Mapped[str | None] = mapped_column(Text)
    estimated_effort: Mapped[str | None] = mapped_column(String(20), comment="1-4 hours | half day | 1-2 days | 3-5 days | 1+ week")
    blocked_by: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", comment="new | interested | pass | doing | closed")
    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (
        Index("idx_cf_opp_repo_issue", "github_repo", "github_issue_number", unique=True),
        Index("idx_cf_opp_status", "status"),
        Index("idx_cf_opp_score", text("score DESC")),
    )


class ContributionFeedback(Base):
    """Per-user feedback on contribution opportunities."""

    __tablename__ = "contribution_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contribution_opportunities.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    signal: Mapped[str] = mapped_column(String(20), nullable=False, comment="interested | pass | doing")
    feedback_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (
        Index("idx_cf_feedback_user_opp", "user_id", "opportunity_id"),
    )


class ContributionRepo(Base):
    """Tracked repos (Aaliyah's manual additions)."""

    __tablename__ = "contribution_repos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    github_full_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, comment="owner/repo")
    language: Mapped[str] = mapped_column(String(50), nullable=False, default="python")
    topic_hint: Mapped[str | None] = mapped_column(String(100))
    added_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (Index("idx_cf_repo_name", "github_full_name"),)
