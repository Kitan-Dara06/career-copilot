"""SQLAlchemy declarative base for all v0.1 models."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all Career Copilot ORM models."""

    # Qdrant is used for vector storage instead of pgvector.
    # All vector data lives in Qdrant collections; PostgreSQL stores
    # Qdrant point IDs and associated metadata.
