"""Path constants for the project."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
"""Top-level project directory (contains pyproject.toml, .env, etc.)."""

DATA_DIR = PROJECT_ROOT / "data"
"""User data directory (gitignored, seeded profiles, local state)."""

PROMPTS_DIR = PROJECT_ROOT / "agents"
"""Root directory for agent prompt files (agents/<name>/prompts/)."""

CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"
"""Local corpus directory (cached fetches, seed data)."""
