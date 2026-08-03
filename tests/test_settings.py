"""Tests for config/settings.py."""

from __future__ import annotations

import pytest

from career_copilot.config import Settings


def test_load_with_defaults() -> None:
    """Settings loads with default values when no env vars set."""
    settings = Settings(_env_file=None)
    assert settings.voyage_model == "voyage-3"
    assert settings.voyage_embed_dim == 1024
    assert settings.database_url == "postgresql+asyncpg://kitan:dev@localhost:5432/career_copilot"
    assert settings.timezone == "Africa/Lagos"
    assert settings.daily_digest_time == "09:00"


def test_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings reads from environment variables."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-voyage-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-firecrawl-key")
    monkeypatch.setenv("NOTION_API_KEY", "test-notion-key")

    settings = Settings()
    assert settings.telegram_bot_token == "test-token"
    assert settings.voyage_api_key == "test-voyage-key"
    assert settings.gemini_api_key == "test-gemini-key"
    assert settings.deepseek_api_key == "test-deepseek-key"
    assert settings.tavily_api_key == "test-tavily-key"
    assert settings.firecrawl_api_key == "test-firecrawl-key"
    assert settings.notion_api_key == "test-notion-key"


def test_empty_strings_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty strings are valid when no env vars are set."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    # Disable .env file loading to ensure isolation
    settings = Settings(_env_file=None)
    assert settings.telegram_bot_token == ""
    assert settings.tavily_api_key == ""
    assert settings.firecrawl_api_key == ""
