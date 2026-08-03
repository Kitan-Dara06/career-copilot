"""Memory layer — types for the three-tier memory system."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class MemoryLayer(StrEnum):
    """Which tier of memory a record lives in."""

    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class MemoryRecord(BaseModel):
    """A single memory record across any layer."""

    namespace: str
    key: str
    value: Any  # JSON-serializable
    layer: MemoryLayer
    embedding: list[float] | None = None  # 1024-dim Voyage 3 vector (for vector layers)
    metadata: dict[str, object] = {}


class MemoryQuery(BaseModel):
    """Query parameters for retrieving memory records."""

    namespace: str
    key: str | None = None  # exact lookup if provided
    embedding: list[float] | None = None  # for similarity search
    k: int = 5
    ttl_after: datetime | None = None  # for short-term filtering


class MemoryError(Exception):
    """Base exception for memory layer errors."""


class NamespaceAccessError(MemoryError):
    """Raised when an agent tries to access a namespace it doesn't have permission for."""


class MemoryNotFoundError(MemoryError):
    """Raised when a requested memory record doesn't exist."""
