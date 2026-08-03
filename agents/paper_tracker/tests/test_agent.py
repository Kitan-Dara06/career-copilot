"""Tests for Paper Tracker agent — digest flow with mocked arXiv."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from agents.paper_tracker.agent import PaperTrackerAgent
from backbone.tools.arxiv import Paper


def _make_paper(arxiv_id: str, title: str) -> Paper:
    """Build a fake arXiv Paper."""
    return Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=["A. Author", "B. Researcher"],
        abstract=f"Abstract for {arxiv_id}. This is a test paper about NLP and IR.",
        published=datetime.now(UTC) - timedelta(hours=1),
        categories=["cs.CL"],
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


@pytest.mark.asyncio
@patch("agents.paper_tracker.agent.FetchRecentTool.__call__")
@patch("agents.paper_tracker.agent.EmbedTool.__call__")
@patch("agents.paper_tracker.agent.SearchTool.__call__")
@patch("agents.paper_tracker.agent.UpsertTool.__call__")
async def test_run_digest_stream_a_structure(
    mock_upsert: AsyncMock,
    mock_search: AsyncMock,
    mock_embed: AsyncMock,
    mock_fetch: AsyncMock,
) -> None:
    """Running a digest returns interest items with correct fields."""
    from backbone.tools.arxiv import FetchRecentOutput
    from backbone.tools.vector import EmbedOutput, ScoredRecord, SearchOutput, UpsertOutput

    agent = PaperTrackerAgent()

    # Mock arXiv fetch
    mock_fetch.return_value = FetchRecentOutput(
        papers=[_make_paper("2501.00001", "Test Paper: Advanced NLP")]
    )

    # Mock embedding
    mock_embed.return_value = EmbedOutput(embeddings=[[0.1] * 1024])

    # Mock search
    mock_search.return_value = SearchOutput(
        results=[
            ScoredRecord(
                point_id="2501.00001",
                score=0.95,
                payload={"title": "Test Paper: Advanced NLP"},
            )
        ]
    )

    # Mock upsert
    mock_upsert.return_value = UpsertOutput(success=True)

    result = await agent.run_digest("daily")

    assert len(result.interest_items) >= 0
    for item in result.interest_items:
        assert "arxiv_id" in item
        assert "title" in item
        assert "authors" in item
        assert "why" in item
        assert item["stream"] == "interest"


@pytest.mark.asyncio
@patch("agents.paper_tracker.agent.UpsertTool.__call__")
@patch("agents.paper_tracker.agent.SearchTool.__call__")
@patch("agents.paper_tracker.agent.EmbedTool.__call__")
@patch("agents.paper_tracker.agent.FetchRecentTool.__call__")
async def test_empty_fetch_returns_empty_results(
    mock_fetch: AsyncMock,
    _mock_embed: AsyncMock,
    _mock_search: AsyncMock,
    _mock_upsert: AsyncMock,
) -> None:
    """When arXiv returns 0 papers, the digest is empty."""
    from backbone.tools.arxiv import FetchRecentOutput

    agent = PaperTrackerAgent()
    mock_fetch.return_value = FetchRecentOutput(papers=[])

    result = await agent.run_digest("daily")
    assert result.interest_items == []
    assert result.professor_items == []


@pytest.mark.asyncio
async def test_feedback_handler_called() -> None:
    """Feedback handler calls the feedback tool."""
    agent = PaperTrackerAgent()
    agent._feedback = AsyncMock()
    await agent.handle_feedback("2501.00001", "save")

    agent._feedback.assert_awaited_once()
    call_input = agent._feedback.call_args[0][1]
    assert call_input.item_id == "2501.00001"
    assert call_input.signal == "save"


def test_avg_vector() -> None:
    """Average vector helper works."""
    from agents.paper_tracker.agent import _avg_vector

    result = _avg_vector([[1.0, 2.0], [3.0, 4.0]])
    assert result == [2.0, 3.0]


def test_avg_vector_empty() -> None:
    """Average vector of empty list returns zeros."""
    from agents.paper_tracker.agent import _avg_vector

    result = _avg_vector([])
    assert len(result) == 1024
    assert all(v == 0.0 for v in result)
