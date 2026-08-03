"""Tests for working memory (in-process dict)."""

from __future__ import annotations

import pytest

from backbone.memory.types import MemoryLayer, MemoryNotFoundError, MemoryRecord
from backbone.memory.working import clear, get, set, size


def test_set_and_get() -> None:
    """Setting then getting returns the same record."""
    record = MemoryRecord(
        namespace="test",
        key="test-key",
        value={"hello": "world"},
        layer=MemoryLayer.WORKING,
    )
    set("task-1", record)

    fetched = get("task-1", "test-key")
    assert fetched.key == "test-key"
    assert fetched.value == {"hello": "world"}
    assert fetched.layer == MemoryLayer.WORKING
    assert fetched.namespace == "test"


def test_get_missing_key_raises() -> None:
    """Getting a non-existent key raises MemoryNotFoundError."""
    with pytest.raises(MemoryNotFoundError):
        get("task-nonexistent", "no-key")


def test_clear_removes_records() -> None:
    """Clearing a task removes all its records."""
    record = MemoryRecord(
        namespace="test",
        key="a",
        value=1,
        layer=MemoryLayer.WORKING,
    )
    set("task-clear", record)
    assert size() > 0

    clear("task-clear")
    with pytest.raises(MemoryNotFoundError):
        get("task-clear", "a")


def test_size_tracks_task_count() -> None:
    """Size returns number of active task stores."""
    start = size()
    set("task-size-1", MemoryRecord(namespace="ns", key="k", value=1, layer=MemoryLayer.WORKING))
    set("task-size-2", MemoryRecord(namespace="ns", key="k", value=2, layer=MemoryLayer.WORKING))
    assert size() == start + 2
    clear("task-size-1")
    assert size() == start + 1
    clear("task-size-2")
    assert size() == start
