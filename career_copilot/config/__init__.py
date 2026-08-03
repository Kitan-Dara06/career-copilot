"""Configuration package: settings, logging, and path constants."""

from .logging import configure_logging
from .paths import CORPUS_DIR, DATA_DIR, PROJECT_ROOT, PROMPTS_DIR
from .settings import Settings, get_settings

__all__ = [
    "Settings",
    "get_settings",
    "configure_logging",
    "PROJECT_ROOT",
    "DATA_DIR",
    "PROMPTS_DIR",
    "CORPUS_DIR",
]
