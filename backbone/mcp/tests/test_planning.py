"""Tests for the planning read adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from backbone.mcp.planning import (
    get_active_workspace_id,
    get_summary,
    get_workspace,
    list_artifacts,
    list_decisions,
    list_goals,
    list_notes,
    list_tasks,
    list_workspaces,
)


# ── Reuse the fake session pattern from test_adapters ──


class _FakeRow(SimpleNamespace):
    pass


class _FakeResult:
    def __init__(self, rows: Any) -> None:
        # _single=True means rows is a single row object
        # _single=False means rows is a list of rows
        if isinstance(rows, list):
            self._rows = rows
            self._single = False
        else:
            self._rows = [rows] if rows is not None else []
            self._single = True

    def one_or_none(self) -> Any:
        if self._single:
            return self._rows[0] if self._rows else None
        # When the caller passed a list, return the first element (None if empty)
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSessionFactory:
    """Mimics ``async_session_factory``: a function returning a sessionmaker.

    The first ``async_session_factory()()`` call returns a sessionmaker that
    yields sessions sharing the same result queue. Each call to the same
    sessionmaker returns the same instance so multiple ``async with`` blocks
    draw from a shared per-test query pool.
    """

    def __init__(self, results: list[Any]) -> None:
        self._queue = list(results)
        self._shared_maker = _FakeSessionMaker(self._queue)

    def __call__(self) -> "_FakeSessionMaker":
        return self._shared_maker


class _FakeSessionMaker:
    """Used as ``async with maker as session:``."""

    def __init__(self, queue: list[Any]) -> None:
        self._queue = queue

    async def __aenter__(self) -> "_FakeSession":
        return _FakeSession(self._queue)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeSession:
    def __init__(self, queue: list[Any]) -> None:
        self._queue = queue

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        if not self._queue:
            return _FakeResult([])
        return _FakeResult(self._queue.pop(0))


def _patch(monkeypatch: Any, results: list[Any]) -> None:
    import backbone.db.session as db_session

    # One shared factory per test so every ``_session_factory()()`` in a query
    # chain (e.g. get_summary) draws from the same sequential result pool.
    factory = _FakeSessionFactory(results)
    monkeypatch.setattr(
        db_session,
        "async_session_factory",
        lambda: factory,
    )


# ── workspaces ──


async def test_list_workspaces(monkeypatch: Any) -> None:
    _patch(
        monkeypatch,
        [
            [_FakeRow(id=1, name="Master's 2027", intake_year=2027, target_degree="MSc", owner="aaliyah", status="active", created_at=datetime.now(UTC))],
        ],
    )
    rows = await list_workspaces()
    assert len(rows) == 1
    assert rows[0]["name"] == "Master's 2027"
    assert rows[0]["intake_year"] == 2027


async def test_get_workspace_returns_provenance(monkeypatch: Any) -> None:
    _patch(
        monkeypatch,
        [
            _FakeRow(id=2, name="EU apps", intake_year=2026, target_degree="MSc", owner="aaliyah", status="active", created_at=datetime.now(UTC)),
        ],
    )
    ws = await get_workspace(2)
    assert ws is not None
    assert "provenance" in ws
    assert "planning_workspaces:id=2" in ws["provenance"]["sources"]


async def test_get_workspace_not_found(monkeypatch: Any) -> None:
    _patch(monkeypatch, [[]])
    assert await get_workspace(999) is None


# ── goals / tasks / decisions / notes / artifacts ──


async def test_list_goals_filters_status(monkeypatch: Any) -> None:
    _patch(
        monkeypatch,
        [
            [_FakeRow(id=1, workspace_id=1, title="Documents", description=None, parent_id=None, priority=1, status="open", created_at=datetime.now(UTC))],
        ],
    )
    rows = await list_goals(1, status="open")
    assert rows[0]["title"] == "Documents"
    assert rows[0]["priority"] == 1


async def test_list_tasks_with_due_date_filter(monkeypatch: Any) -> None:
    _patch(
        monkeypatch,
        [
            [_FakeRow(id=1, goal_id=1, workspace_id=1, title="Verify GRE", description=None, due_date=datetime.now(UTC).date(), status="todo", blocked_by_task_id=None, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))],
        ],
    )
    rows = await list_tasks(1, due_before="2026-12-01")
    assert rows[0]["title"] == "Verify GRE"
    assert rows[0]["due_date"] is not None


async def test_list_decisions(monkeypatch: Any) -> None:
    _patch(
        monkeypatch,
        [
            [_FakeRow(id=1, workspace_id=1, title="Skip GRE", rationale="Cost", status="confirmed", evidence={"sources": ["a"], "retrieved_at": "now"}, decided_at=datetime.now(UTC), superseded_by_id=None)],
        ],
    )
    rows = await list_decisions(1)
    assert rows[0]["status"] == "confirmed"
    assert rows[0]["evidence"]["sources"] == ["a"]


async def test_list_notes_pinned_first(monkeypatch: Any) -> None:
    _patch(
        monkeypatch,
        [
            [_FakeRow(id=1, workspace_id=1, kind="observation", body="x", pinned=True, created_at=datetime.now(UTC))],
        ],
    )
    rows = await list_notes(1)
    assert rows[0]["pinned"] is True


async def test_list_artifacts(monkeypatch: Any) -> None:
    _patch(
        monkeypatch,
        [
            [_FakeRow(id=1, workspace_id=1, type="reading_plan", title="RAG eval", body={"items": []}, evidence=None, version=1, status="draft", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))],
        ],
    )
    rows = await list_artifacts(1, artifact_type="reading_plan")
    assert rows[0]["type"] == "reading_plan"
    assert rows[0]["version"] == 1


# ── state + summary ──


async def test_get_active_workspace_id(monkeypatch: Any) -> None:
    _patch(monkeypatch, [[_FakeRow(active_workspace_id=42)]])
    assert await get_active_workspace_id("chat-1") == 42


async def test_get_active_workspace_id_no_row(monkeypatch: Any) -> None:
    _patch(monkeypatch, [[]])
    assert await get_active_workspace_id("chat-x") is None


async def test_get_summary_returns_counts_and_provenance(monkeypatch: Any) -> None:
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    _patch(
        monkeypatch,
        [
            # workspace
            [_FakeRow(id=1, name="Master's 2027", intake_year=2027, target_degree="MSc", owner="aaliyah", status="active", created_at=datetime.now(UTC))],
            # goals
            [
                _FakeRow(id=10, workspace_id=1, title="Funding", description=None, parent_id=None, priority=1, status="open", created_at=datetime.now(UTC)),
                _FakeRow(id=11, workspace_id=1, title="Stale", description=None, parent_id=None, priority=0, status="done", created_at=datetime.now(UTC)),
            ],
            # tasks
            [
                _FakeRow(id=20, goal_id=10, workspace_id=1, title="Get LoR 1", description=None, due_date=yesterday, status="todo", blocked_by_task_id=None, created_at=datetime.now(UTC), updated_at=datetime.now(UTC)),
                _FakeRow(id=21, goal_id=10, workspace_id=1, title="Write SoP", description=None, due_date=None, status="todo", blocked_by_task_id=None, created_at=datetime.now(UTC), updated_at=datetime.now(UTC)),
            ],
            # decisions
            [_FakeRow(id=30, workspace_id=1, title="Skip GRE", rationale="Cost", status="confirmed", evidence=None, decided_at=datetime.now(UTC), superseded_by_id=None)],
        ],
    )

    summary = await get_summary(1)
    assert summary["open_goals_count"] == 1
    assert summary["open_goals_titles"] == ["Funding"]
    assert summary["overdue_tasks_count"] == 1
    assert summary["overdue_tasks_titles"] == ["Get LoR 1"]
    assert summary["total_tasks_open"] == 2
    assert summary["confirmed_decisions_titles"] == ["Skip GRE"]
    assert "provenance" in summary
    assert summary["workspace"]["name"] == "Master's 2027"
