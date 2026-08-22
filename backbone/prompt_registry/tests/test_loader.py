"""Tests for prompt loader."""

from __future__ import annotations

import pytest

from backbone.prompt_registry.loader import (
    clear_cache,
    load,
    render,
)


def setup_method() -> None:
    clear_cache()


def test_load_latest() -> None:
    """Loading with version='latest' resolves the highest version."""
    prompt = load("paper_tracker", "why_relevant")
    assert prompt.name == "why_relevant"
    assert prompt.version == 2  # v2 is higher than v1


def test_load_explicit_version() -> None:
    """Loading with an explicit version returns that version."""
    prompt = load("paper_tracker", "why_relevant", version=1)
    assert prompt.version == 1


def test_load_missing_raises() -> None:
    """Loading a non-existent prompt raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load("paper_tracker", "nonexistent_prompt")


def test_render_substitutes_correctly() -> None:
    """Render fills in placeholders from the template."""
    prompt = load("paper_tracker", "why_relevant", version=1)
    rendered, input_hash = render(
        prompt,
        {
            "title": "Test Paper",
            "abstract": "This is test abstract.",
            "interests": "NLP, IR",
        },
    )
    assert "Test Paper" in rendered
    assert "This is test abstract." in rendered
    assert "NLP, IR" in rendered
    assert len(input_hash) == 64  # SHA-256 hex


def test_render_missing_key_raises() -> None:
    """Render raises KeyError when a placeholder is missing."""
    prompt = load("paper_tracker", "why_relevant", version=1)
    with pytest.raises(KeyError):
        render(prompt, {"title": "Missing fields"})


def test_model_config_defaults() -> None:
    """Model config loads from YAML — temperature and max_tokens read correctly."""
    prompt = load("paper_tracker", "why_relevant", version=1)
    assert prompt.model.temperature == 0.3
    assert prompt.model.max_tokens == 80
    assert prompt.model.name == "deepseek-v4-flash"  # Assigned per model mapping
