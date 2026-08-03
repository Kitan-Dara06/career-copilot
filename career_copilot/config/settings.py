"""Pydantic Settings — loads from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    All required keys are validated at startup. The application refuses
    to boot if a required key is missing.
    """

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ── Telegram ──
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Voyage AI ──
    voyage_api_key: str = ""
    voyage_model: str = "voyage-3"
    voyage_embed_dim: int = 1024

    # ── Gemini ──
    gemini_api_key: str = ""

    # ── DeepSeek ──
    deepseek_api_key: str = ""

    # ── Tavily ──
    tavily_api_key: str = ""

    # ── Semantic Scholar ──
    semantic_scholar_api_key: str = ""

    # ── Firecrawl ──
    firecrawl_api_key: str = ""

    # ── Notion ──
    notion_api_key: str = ""
    notion_papers_db_id: str = ""
    notion_professors_db_id: str = ""

    # ── Database ──
    database_url: str = "postgresql+asyncpg://kitan:dev@localhost:5432/career_copilot"
    database_echo: bool = False

    # ── Redis / Celery ──
    upstash_redis_url: str = ""

    # ── Modal GPU ──
    modal_environment: str = "dev"
    modal_app_name: str = "career-copilot"
    brief_model: str = "qwen-3.6"
    brief_gpu: str = "A10G"
    # When True, /prof briefs route to the Modal Qwen worker (deploy/modal/brief_worker.py).
    # When False (default for local dev), briefs fall back to Gemini with strict JSON output.
    brief_via_modal: bool = False

    # ── Qdrant Cloud ──
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    # ── Azure ──
    azure_connection_string: str = ""
    applicationinsights_connection_string: str = ""

    # ── Scheduling ──
    timezone: str = "Africa/Lagos"
    daily_digest_time: str = "09:00"
    weekly_digest_day: str = "Sunday"
    weekly_digest_time: str = "20:00"

    # ── Professor discovery regions ──
    # Comma-separated region buckets kept in /discover output. Region codes:
    #   US, CA, EU, CN, HK, UK, OTHER.
    # UK is excluded by default. HK and CN are surfaced separately so a Hong
    # Kong professor does not crowd out mainland-China candidates.
    discover_regions: str = "US,CA,EU,CN,HK"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the singleton Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
