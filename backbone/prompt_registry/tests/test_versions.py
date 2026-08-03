"""Tests for prompt version management."""

from __future__ import annotations

from backbone.prompt_registry.versions import compare, list_versions


def test_list_versions() -> None:
    """List versions returns sorted integers."""
    versions = list_versions("paper_tracker", "why_relevant")
    assert len(versions) >= 2
    assert versions == [1, 2]


def test_compare_finds_template_diff() -> None:
    """Compare two versions detects template differences."""
    diff = compare("paper_tracker", "why_relevant", 1, 2)
    assert diff.template_diff is True
    assert "template changed" in diff.changes


def test_compare_finds_model_diff() -> None:
    """Compare detects model config changes (temperature 0.3 → 0.2)."""
    diff = compare("paper_tracker", "why_relevant", 1, 2)
    assert diff.model_diff is True


def test_compare_finds_schema_diff() -> None:
    """Compare detects input schema changes (v2 adds prior_papers field)."""
    diff = compare("paper_tracker", "why_relevant", 1, 2)
    assert diff.schema_diff is True
