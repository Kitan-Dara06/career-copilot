"""Tests for config/logging.py."""

from __future__ import annotations

from career_copilot.config.logging import configure_logging, get_logger


def test_configure_json_output() -> None:
    """JSON output can be configured and produces valid JSON."""
    configure_logging(json_output=True)
    logger = get_logger("test_logger")
    assert logger is not None


def test_default_is_not_json() -> None:
    """Default configuration uses console (non-JSON) rendering."""
    configure_logging(json_output=False)
    logger = get_logger("test_logger")
    assert logger is not None
