"""Working memory — in-process dict, scoped to a single task run.

Cleared automatically at the end of each task.
"""

from __future__ import annotations

from .types import MemoryNotFoundError, MemoryRecord

_store: dict[str, dict[str, MemoryRecord]] = {}
"""{task_id: {key: MemoryRecord}}"""


def set(task_id: str, record: MemoryRecord) -> None:
    """Store a record in working memory for the given task.

    Args:
        task_id: The task this record belongs to.
        record: The memory record to store.
    """
    if task_id not in _store:
        _store[task_id] = {}
    _store[task_id][record.key] = record


def get(task_id: str, key: str) -> MemoryRecord:
    """Retrieve a record from working memory.

    Args:
        task_id: The task the record belongs to.
        key: The record key.

    Returns:
        The matching MemoryRecord.

    Raises:
        MemoryNotFoundError: If the key doesn't exist for this task.
    """
    task_store = _store.get(task_id)
    if task_store is None:
        raise MemoryNotFoundError(f"No working memory for task {task_id!r}")
    record = task_store.get(key)
    if record is None:
        raise MemoryNotFoundError(f"Key {key!r} not found in working memory for task {task_id!r}")
    return record


def clear(task_id: str) -> None:
    """Clear all working memory for a task.

    Called by the dispatcher at task end.
    """
    _store.pop(task_id, None)


def size() -> int:
    """Return the number of task stores currently in working memory."""
    return len(_store)
