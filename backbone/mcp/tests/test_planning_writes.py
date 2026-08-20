"""Tests for the planning write side (proposal + executor)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backbone.mcp import planning_writes as pw

# ── Fake session machinery ─────────────────────────────────────────
# Mirrors the double-call pattern used in production:
#   async_session_factory() -> _FakeFactory (callable)
#   factory()              -> _FakeSessionMaker (async ctx-manager)
#   async with maker       -> _FakeSession
# One shared result queue + SQL log per test.


class _Result:
    def __init__(self, scalar: Any = None, row: Any = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._row = row
        self._rows = rows or []

    def scalar_one(self) -> Any:
        return self._scalar

    def one_or_none(self) -> Any:
        return self._row

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    def __init__(self, queue: list[_Result], log: list[tuple[str, dict]]) -> None:
        self._queue = queue
        self._log = log
        self.commits = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self._log.append((str(stmt), params or {}))
        return self._queue.pop(0) if self._queue else _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


class _FakeSessionMaker:
    def __init__(self, queue: list[_Result], log: list[tuple[str, dict]]) -> None:
        self._queue = queue
        self._log = log

    async def __aenter__(self) -> _FakeSession:
        return _FakeSession(self._queue, self._log)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeFactory:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.log: list[tuple[str, dict]] = []
        self._shared = _FakeSessionMaker(self.results, self.log)

    def __call__(self) -> _FakeSessionMaker:
        return self._shared


def _patch(monkeypatch: Any, results: list[_Result]) -> _FakeFactory:
    import backbone.db.session as db_session

    factory = _FakeFactory(results)
    monkeypatch.setattr(db_session, "async_session_factory", lambda: factory)
    return factory


def _proposal_row(**over: Any) -> SimpleNamespace:
    base = {
        "id": 1,
        "chat_id": "aaliyah",
        "workspace_id": 1,
        "tool": "planning.add_goal",
        "args": {},
        "summary": "Add goal: Test",
        "risk_level": "medium",
        "status": "pending",
    }
    base.update(over)
    return SimpleNamespace(**base)


# ── Risk mapping ───────────────────────────────────────────────────


def test_risk_level_for() -> None:
    assert pw.risk_level_for("planning.create_workspace") == "high"
    assert pw.risk_level_for("planning.add_goal") == "medium"
    assert pw.risk_level_for("planning.add_note") == "low"
    assert pw.risk_level_for("planning.unknown_tool") == "medium"


# ── Proposal creation ──────────────────────────────────────────────


async def test_create_proposal_medium_risk_stays_pending(monkeypatch: Any) -> None:
    factory = _patch(monkeypatch, [_Result(scalar=5)])
    result = await pw.propose_add_goal(workspace_id=1, title="Funding", priority=2)

    assert result["proposal_id"] == 5
    assert result["status"] == "pending"
    assert result["risk_level"] == "medium"
    assert result["summary"] == "Add goal: Funding"
    assert any("planning_proposals" in sql for sql, _ in factory.log)


async def test_create_proposal_low_risk_auto_applies(monkeypatch: Any) -> None:
    factory = _patch(
        monkeypatch,
        [
            _Result(scalar=10),  # INSERT proposal
            _Result(row=_proposal_row(id=10, tool="planning.add_note", risk_level="low")),
            _Result(scalar=77),  # INSERT note
            _Result(),  # UPDATE status -> approved
        ],
    )
    result = await pw.propose_add_note(workspace_id=1, body="Pinned thought", pinned=True)

    assert result["status"] == "approved"
    assert result["result_id"] == 77
    assert result["proposal_id"] == 10
    sqls = " ".join(sql for sql, _ in factory.log)
    assert "planning_notes" in sqls
    assert "approved" in sqls


# ── apply / skip ───────────────────────────────────────────────────


async def test_apply_proposal_runs_executor(monkeypatch: Any) -> None:
    factory = _patch(
        monkeypatch,
        [
            _Result(
                row=_proposal_row(
                    id=3,
                    tool="planning.add_task",
                    args={"goal_id": 2, "title": "Write SoP"},
                )
            ),
            _Result(scalar=99),  # INSERT task
            _Result(),  # UPDATE status
        ],
    )
    result = await pw.apply_proposal(3)

    assert result["status"] == "approved"
    assert result["result_id"] == 99
    sqls = " ".join(sql for sql, _ in factory.log)
    assert "planning_tasks" in sqls
    assert "UPDATE planning_proposals SET status = 'approved'" in sqls


async def test_apply_proposal_unknown_tool_raises(monkeypatch: Any) -> None:
    _patch(monkeypatch, [_Result(row=_proposal_row(tool="planning.nope"))])
    with pytest.raises(ValueError):
        await pw.apply_proposal(1)


async def test_apply_proposal_already_applied_raises(monkeypatch: Any) -> None:
    _patch(monkeypatch, [_Result(row=_proposal_row(status="approved"))])
    with pytest.raises(ValueError):
        await pw.apply_proposal(1)


async def test_skip_proposal(monkeypatch: Any) -> None:
    _patch(
        monkeypatch,
        [_Result(row=SimpleNamespace(id=4, tool="planning.add_goal", summary="Add goal: X"))],
    )
    result = await pw.skip_proposal(4)
    assert result["status"] == "skipped"
    assert result["proposal_id"] == 4
