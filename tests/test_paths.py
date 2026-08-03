"""Tests for config/paths.py."""

from __future__ import annotations

from career_copilot.config.paths import (
    CORPUS_DIR,
    DATA_DIR,
    PROJECT_ROOT,
    PROMPTS_DIR,
)


def test_project_root_exists() -> None:
    """PROJECT_ROOT points to an existing directory."""
    assert PROJECT_ROOT.exists()
    assert (PROJECT_ROOT / "pyproject.toml").exists()


def test_data_dir() -> None:
    """DATA_DIR is a subdirectory of PROJECT_ROOT."""
    assert DATA_DIR == PROJECT_ROOT / "data"
    assert str(DATA_DIR).endswith("data")


def test_prompts_dir() -> None:
    """PROMPTS_DIR points to the agents directory."""
    assert PROMPTS_DIR == PROJECT_ROOT / "agents"
    assert PROMPTS_DIR.exists()


def test_corpus_dir() -> None:
    """CORPUS_DIR is a subdirectory of DATA_DIR."""
    assert CORPUS_DIR == DATA_DIR / "corpus"
    assert str(CORPUS_DIR).endswith("corpus")
